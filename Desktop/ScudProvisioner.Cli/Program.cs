using ScudProvisioner.Models;
using ScudProvisioner.Services;

namespace ScudProvisioner.Cli;

/// <summary>
/// Cross-platform (net9.0) console front-end for the SCUD reader provisioner.
/// It reuses the EXACT same provisioning logic as the Windows MAUI GUI by
/// constructing the shared Core services and invoking <see cref="ProvisionFlow.RunAsync"/>.
/// No provisioning logic is reimplemented here.
/// </summary>
internal static class Program
{
    private const string Usage =
        """
        scud-provision — provision a SCUD reader over serial (cross-platform CLI)

        Usage:
          scud-provision --port <PORT> --server <URL> --admin-api-key <KEY> \
                         --group-id <UUID> --display-name <NAME> [options]

        Required:
          --port <PORT>            Serial port (e.g. COM5 or /dev/ttyUSB0)
          --server <URL>           Backend base URL (e.g. http://localhost:8000)
          --admin-api-key <KEY>    Admin API key (sent as X-Api-Key)
          --group-id <UUID>        Reader-group id (UUID)
          --display-name <NAME>    Human-readable reader name

        Optional:
          --baud <N>               Serial baud rate (default 115200)
          --description <TEXT>     Reader description
          --profile-id <UUID>      Server reader-profile id. When set, the reader is
                                   enrolled with this profile and the backend-resolved
                                   config script is played. When omitted, the local
                                   built-in device-config template is used.
          --lock-duration-ms <N>   Lock impulse duration, ms (default 3000)
          -h, --help               Show this help and exit
        """;

    private static async Task<int> Main(string[] args)
    {
        if (args.Length == 0 || args.Contains("-h") || args.Contains("--help"))
        {
            Console.WriteLine(Usage);
            return args.Length == 0 ? 1 : 0;
        }

        Dictionary<string, string> opts;
        try
        {
            opts = ParseArgs(args);
        }
        catch (ArgumentException ex)
        {
            Console.Error.WriteLine($"ERROR: {ex.Message}");
            Console.Error.WriteLine();
            Console.Error.WriteLine(Usage);
            return 2;
        }

        // ── required options ──────────────────────────────────────────────────
        var missing = new[] { "port", "server", "admin-api-key", "group-id", "display-name" }
            .Where(k => string.IsNullOrWhiteSpace(Opt(opts, k)))
            .ToList();
        if (missing.Count > 0)
        {
            Console.Error.WriteLine($"ERROR: missing required option(s): {string.Join(", ", missing.Select(m => "--" + m))}");
            Console.Error.WriteLine();
            Console.Error.WriteLine(Usage);
            return 2;
        }

        var port        = Opt(opts, "port")!;
        var server      = Opt(opts, "server")!;
        var apiKey      = Opt(opts, "admin-api-key")!;
        var groupId     = Opt(opts, "group-id")!;
        var displayName = Opt(opts, "display-name")!;
        var description = Opt(opts, "description");
        var profileId   = Opt(opts, "profile-id");

        if (!Guid.TryParse(groupId, out _))
        {
            Console.Error.WriteLine($"ERROR: --group-id is not a valid UUID: {groupId}");
            return 2;
        }

        int baud = 115200, lockMs = 3000;
        if (Opt(opts, "baud") is { } baudStr && !int.TryParse(baudStr, out baud))
        {
            Console.Error.WriteLine($"ERROR: --baud is not an integer: {baudStr}");
            return 2;
        }
        if (Opt(opts, "lock-duration-ms") is { } lockStr && !int.TryParse(lockStr, out lockMs))
        {
            Console.Error.WriteLine($"ERROR: --lock-duration-ms is not an integer: {lockStr}");
            return 2;
        }

        // ── wire up the SAME Core services the GUI uses ───────────────────────
        IKeyValueStore store = new FileKeyValueStore();
        var settings = new SettingsService(store)
        {
            BackendUrl = server,
            AdminApiKey = apiKey,
            Baud = baud,
            LockDurationMs = lockMs,
        };
        var api = new BackendApi(settings);
        using var serial = new SerialClient();
        var templates = new ConfigTemplateService(store);
        var flow = new ProvisionFlow(api, serial, settings, templates);

        var log = new Progress<string>(Console.WriteLine);
        using var cts = new CancellationTokenSource();
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };

        // ── resolve optional server profile ───────────────────────────────────
        ReaderProfileDto? profile = null;
        if (!string.IsNullOrWhiteSpace(profileId))
        {
            try
            {
                var profiles = await api.ListReaderProfilesAsync(cts.Token);
                profile = profiles.FirstOrDefault(p =>
                    string.Equals(p.ProfileId, profileId, StringComparison.OrdinalIgnoreCase));
                if (profile is null)
                {
                    Console.Error.WriteLine($"ERROR: profile-id '{profileId}' not found on server.");
                    return 3;
                }
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine($"ERROR: failed to load reader profiles: {ex.Message}");
                return 3;
            }
        }

        var input = new ProvisionInput
        {
            PortName = port,
            Group = new ReaderGroup { GroupId = groupId },
            DisplayName = displayName.Trim(),
            Description = string.IsNullOrWhiteSpace(description) ? null : description!.Trim(),
            Profile = profile,
            HardwareClass = profile?.HardwareClass,
        };

        try
        {
            var result = await flow.RunAsync(input, log, cts.Token);
            Console.WriteLine();
            Console.WriteLine($"OK reader_id={result.ReaderIdHex}");
            return 0;
        }
        catch (OperationCanceledException)
        {
            Console.Error.WriteLine("ERROR: cancelled.");
            return 130;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine($"ERROR: {ex.Message}");
            return 1;
        }
    }

    /// <summary>
    /// Minimal dependency-light parser. Accepts "--key value" and "--key=value".
    /// Keys are stored without the leading "--". Unknown/extra tokens are an error.
    /// </summary>
    private static Dictionary<string, string> ParseArgs(string[] args)
    {
        var opts = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        for (int i = 0; i < args.Length; i++)
        {
            var tok = args[i];
            if (!tok.StartsWith("--", StringComparison.Ordinal))
                throw new ArgumentException($"unexpected token '{tok}' (expected --option)");

            var body = tok[2..];
            string key, value;
            int eq = body.IndexOf('=');
            if (eq >= 0)
            {
                key = body[..eq];
                value = body[(eq + 1)..];
            }
            else
            {
                key = body;
                if (i + 1 >= args.Length || args[i + 1].StartsWith("--", StringComparison.Ordinal))
                    throw new ArgumentException($"option --{key} requires a value");
                value = args[++i];
            }

            if (string.IsNullOrEmpty(key))
                throw new ArgumentException($"malformed option '{tok}'");
            opts[key] = value;
        }
        return opts;
    }

    private static string? Opt(Dictionary<string, string> opts, string key)
        => opts.TryGetValue(key, out var v) ? v : null;
}
