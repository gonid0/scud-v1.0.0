using System.Text.RegularExpressions;
using ScudProvisioner.Models;

namespace ScudProvisioner.Services;

public sealed class ProvisionInput
{
    public required string PortName { get; init; }
    public required ReaderGroup Group { get; init; }
    public required string DisplayName { get; init; }
    public string? Description { get; init; }
    // Phase 1: optional server profile. When set, the reader is enrolled with this
    // profile_id/hardware_class and the backend-resolved SET-* script is played
    // verbatim; when null, the local device-config template is used (Phase 4).
    public ReaderProfileDto? Profile { get; init; }
    public string? HardwareClass { get; init; }
}

public sealed class ProvisionResult
{
    public required string ReaderIdHex { get; init; }
    public required string ReaderPubkeyHex { get; init; }
    public required string ServerEdPubkeyHex { get; init; }
    public required string ServerXPubkeyHex { get; init; }
}

public sealed class ProvisionFlow
{
    private static readonly Regex PubkeyRx =
        new(@"pubkey=([0-9a-fA-F]{64})", RegexOptions.Compiled);

    private readonly IBackendApi _api;
    private readonly ISerialClient _serial;
    private readonly ISettingsService _settings;
    private readonly IConfigTemplateService _templates;

    public ProvisionFlow(
        IBackendApi api,
        ISerialClient serial,
        ISettingsService settings,
        IConfigTemplateService templates)
    {
        _api = api;
        _serial = serial;
        _settings = settings;
        _templates = templates;
    }

    public async Task<ProvisionResult> RunAsync(
        ProvisionInput input,
        IProgress<string> log,
        CancellationToken ct = default)
    {
        log.Report($"Opening {input.PortName} @ {_settings.Baud} baud...");
        _serial.Open(input.PortName, _settings.Baud);

        try
        {
            log.Report("[1/4] Generating reader keypair on device...");
            await Send("GEN-KEYPAIR", log, ct, TimeSpan.FromMilliseconds(800));

            var resp = await Send("SHOW-PUBKEY", log, ct);
            var match = PubkeyRx.Match(resp);
            if (!match.Success)
                throw new InvalidOperationException(
                    "SHOW-PUBKEY did not return a 64-hex pubkey. Raw response:\n" + resp);
            var readerPubkey = Convert.FromHexString(match.Groups[1].Value);
            log.Report($"    reader pubkey = {match.Groups[1].Value}");

            var readerIdBytes = Guid.NewGuid().ToByteArray();
            var readerIdHex = Convert.ToHexString(readerIdBytes).ToLowerInvariant();

            // group_id_bytes — те же 16 байт UUID, но в виде hex для UART-команды.
            var groupGuid = Guid.Parse(input.Group.GroupId);
            var groupIdHex = Convert.ToHexString(groupGuid.ToByteArray()).ToLowerInvariant();

            log.Report("[2/4] Enrolling reader at backend...");
            // Phase 1: when a server profile is selected, enroll with profile_id +
            // hardware_class so the backend stores the reader's profile and the
            // resolved config-script (played below) matches. When no profile is
            // selected these fields are null and the enroll is identity-only as before.
            var enroll = await _api.EnrollReaderAsync(new EnrollRequest
            {
                ReaderIdHex = readerIdHex,
                ReaderPubkeyBase64 = Convert.ToBase64String(readerPubkey),
                ReaderGroupId = input.Group.GroupId,
                DisplayName = input.DisplayName,
                Description = string.IsNullOrWhiteSpace(input.Description) ? null : input.Description,
                ProfileId = input.Profile?.ProfileId,
                HardwareClass = input.HardwareClass ?? input.Profile?.HardwareClass,
            }, ct);

            var serverEd = Convert.FromBase64String(enroll.ServerEdPubkeyBase64);
            var serverX  = Convert.FromBase64String(enroll.ServerXPubkeyBase64);
            log.Report($"    reader_id          = {readerIdHex}");
            log.Report($"    server Ed25519 pub = {Convert.ToHexString(serverEd).ToLowerInvariant()}");
            log.Report($"    server X25519  pub = {Convert.ToHexString(serverX ).ToLowerInvariant()}");

            log.Report("[3/4] Writing config to device...");
            await Send($"SET-READER-ID {readerIdHex}", log, ct);
            await Send($"SET-GROUP-ID {groupIdHex}", log, ct);
            await Send($"SET-SERVER-ED-PUB {Convert.ToHexString(serverEd).ToLowerInvariant()}", log, ct);
            await Send($"SET-SERVER-X-PUB {Convert.ToHexString(serverX ).ToLowerInvariant()}", log, ct);

            // Config source — Phase 1: a server profile resolves to a SET-* script on
            // the backend (profile defaults + overrides + bounds-clamp + policy), which
            // we play VERBATIM. Otherwise fall back to the local device-config template
            // (Phase 4). Both land all config in NVS authoritatively via the same SET-*.
            if (input.Profile is not null)
            {
                log.Report($"    config source: server profile '{input.Profile.Name}'");
                var cfg = await _api.GetReaderConfigScriptAsync(readerIdHex, ct);
                foreach (var w in cfg.Warnings)
                    log.Report($"    ! {w}");
                foreach (var line in cfg.Script)
                {
                    // arg == null => verb command alone (BLE-ENABLE / BLE-DISABLE);
                    // arg != null => "<command> <arg>".
                    await Send(
                        string.IsNullOrEmpty(line.Arg) ? line.Command : $"{line.Command} {line.Arg}",
                        log, ct);
                }
            }
            else
            {
                // Phase 4 fallback: FULL SET-* series from the active local template
                // (lock_duration_ms is just one catalog entry, no longer special-cased).
                var template = _templates.GetActive();
                log.Report($"    config source: local template '{template.Name}'");
                foreach (var spec in ConfigParamCatalog.All)
                {
                    var value = template.GetValue(spec); // clamped to [Min..Max]
                    if (spec.UsesVerbCommands)
                    {
                        // Verb-toggled params (ble_enabled → BLE-ENABLE / BLE-DISABLE).
                        var cmd = value != 0 ? spec.VerbOnCommand! : spec.VerbOffCommand!;
                        await Send(cmd, log, ct);
                    }
                    else
                    {
                        // Every numeric param (incl. 0/1 bools like handover_required) → "SET-* <value>".
                        await Send($"{spec.SetCommand} {value}", log, ct);
                    }
                }
            }

            await Send($"SET-TIME {DateTimeOffset.UtcNow.ToUnixTimeSeconds()}", log, ct);

            log.Report("[4/4] COMMIT...");
            var commit = await Send("COMMIT", log, ct, TimeSpan.FromMilliseconds(1500));
            if (!commit.Contains("OK", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Device did not acknowledge COMMIT (no \"OK\").");

            log.Report("");
            log.Report("Provisioning complete. Press the RST button on the reader to reboot.");

            return new ProvisionResult
            {
                ReaderIdHex = readerIdHex,
                ReaderPubkeyHex = match.Groups[1].Value.ToLowerInvariant(),
                ServerEdPubkeyHex = Convert.ToHexString(serverEd).ToLowerInvariant(),
                ServerXPubkeyHex = Convert.ToHexString(serverX ).ToLowerInvariant(),
            };
        }
        finally
        {
            _serial.Close();
        }
    }

    private async Task<string> Send(
        string cmd, IProgress<string> log, CancellationToken ct, TimeSpan? readWindow = null)
    {
        log.Report($"> {cmd}");
        var resp = await _serial.SendAsync(cmd, readWindow, ct);
        foreach (var line in resp.Split('\n', StringSplitOptions.RemoveEmptyEntries))
            log.Report("  " + line.TrimEnd('\r'));
        return resp;
    }
}
