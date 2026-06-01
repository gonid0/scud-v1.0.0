namespace ScudProvisioner.Models;

/// <summary>
/// Tier of a reader-config parameter, mirroring the taxonomy in
/// docs/11_reader_config_provisioning.md §1 / §6 (security🔒 windows that must
/// stay coordinated with the backend, operational, hardware-interop).
/// </summary>
public enum ParamTier
{
    Security, // 🔒 coordinate with backend (shared-protocol invariant)
    Ops,
    Hardware,
}

/// <summary>
/// One per-reader operational parameter. Single desktop source of truth.
///
/// IMPORTANT: this catalog mirrors the firmware CFG[] table in
/// ESP32/firmware/src/state/reader_config.cpp (the <c>SET-*</c> commands, the
/// default and the inclusive [Min..Max] clamp range) **byte-for-byte**. Firmware
/// silently clamps out-of-range values; the desktop clamps client-side too and
/// shows the range. Any change to the firmware table MUST move in lockstep here
/// (and vice-versa) — this is part of the shared-protocol invariant.
/// </summary>
public sealed class ParamSpec
{
    public required string Key { get; init; }          // stable key, used in DeviceConfigTemplate.Values + firmware NVS semantics
    public required string SetCommand { get; init; }   // SET-* serial command name (sent as "SET-* <value>")
    public required long Default { get; init; }
    public required long Min { get; init; }
    public required long Max { get; init; }
    public required string Label { get; init; }
    public required ParamTier Tier { get; init; }

    /// <summary>
    /// Rendered as a Switch in the editor (0/1 value). This is a UI hint only —
    /// most bool-like params (e.g. handover_required) are still numeric on the wire
    /// and emit "SET-* &lt;0|1&gt;". See <see cref="UsesVerbCommands"/> for verb-based ones.
    /// </summary>
    public bool IsBool { get; init; }

    /// <summary>
    /// True only for params that have NO "SET-* &lt;n&gt;" form and instead toggle via
    /// dedicated serial verbs (ble_enabled → BLE-ENABLE / BLE-DISABLE).
    /// </summary>
    public bool UsesVerbCommands { get; init; }

    /// <summary>Serial verb for the "on" state when <see cref="UsesVerbCommands"/> (e.g. BLE-ENABLE).</summary>
    public string? VerbOnCommand { get; init; }

    /// <summary>Serial verb for the "off" state when <see cref="UsesVerbCommands"/> (e.g. BLE-DISABLE).</summary>
    public string? VerbOffCommand { get; init; }

    /// <summary>Enum param (small fixed value set). <see cref="EnumLabels"/> maps value → human label.</summary>
    public bool IsEnum { get; init; }

    public IReadOnlyDictionary<long, string>? EnumLabels { get; init; }

    /// <summary>Clamp <paramref name="value"/> into [Min..Max] (matches firmware reader_config_set()).</summary>
    public long Clamp(long value) => value < Min ? Min : (value > Max ? Max : value);

    /// <summary>Human-readable range hint for the editor, e.g. "5..600" or "off / on".</summary>
    public string RangeHint => IsBool ? "off / on" : $"{Min}..{Max}";
}

/// <summary>
/// Static catalog of all 26 provisionable per-reader parameters: the 24 CFG[]
/// rows from firmware reader_config.cpp (the dead xfer_ttl_ms / SET-TRANSFER-TTL
/// row was removed — it had no firmware read site) plus the two already-
/// provisioned params (lock_duration_ms via SET-LOCK-DURATION, ble_enabled via
/// BLE-ENABLE/DISABLE).
///
/// Identity commands (SET-READER-ID / GROUP-ID / SERVER-ED-PUB / SERVER-X-PUB /
/// SET-TIME / COMMIT) are NOT template params and live in ProvisionFlow.
/// </summary>
public static class ConfigParamCatalog
{
    public const string PassageDirKey = "passage_dir";
    public const string LockDurationKey = "lock_duration_ms";
    public const string BleEnabledKey = "ble_enabled";

    private static readonly IReadOnlyDictionary<long, string> PassageDirLabels =
        new Dictionary<long, string> { [1] = "Entry (1)", [2] = "Exit (2)" };

    // Order roughly: identity-adjacent ops first, then security, then hardware —
    // but the editor groups by Tier, so list order here is only the SET-* emit order.
    public static readonly IReadOnlyList<ParamSpec> All = new ParamSpec[]
    {
        // ── already-provisioned (kept) ────────────────────────────────────────
        new() { Key = LockDurationKey, SetCommand = "SET-LOCK-DURATION", Default = 3000, Min = 500, Max = 10000, Label = "Lock impulse, ms (relay open after GRANT)", Tier = ParamTier.Ops },
        new() { Key = BleEnabledKey, SetCommand = "BLE-ENABLE", Default = 1, Min = 0, Max = 1, Label = "BLE radio enabled", Tier = ParamTier.Ops, IsBool = true, UsesVerbCommands = true, VerbOnCommand = "BLE-ENABLE", VerbOffCommand = "BLE-DISABLE" },

        // ── security 🔒 (coordinate with backend policy) ──────────────────────
        new() { Key = "clock_skew", SetCommand = "SET-CLOCK-SKEW", Default = 60, Min = 5, Max = 600, Label = "Clock skew, s (RTC drift vs token)", Tier = ParamTier.Security },
        new() { Key = "nonce_ttl_ms", SetCommand = "SET-NONCE-TTL", Default = 10000, Min = 2000, Max = 60000, Label = "Nonce TTL, ms (anti-replay window)", Tier = ParamTier.Security },
        new() { Key = "ts_drift", SetCommand = "SET-TSYNC-DRIFT", Default = 10, Min = 1, Max = 60, Label = "Time-sync SOFT drift, s/day", Tier = ParamTier.Security },
        new() { Key = "ts_boot", SetCommand = "SET-TSYNC-BOOTSTRAP", Default = 86400, Min = 3600, Max = 604800, Label = "Time-sync bootstrap window, s", Tier = ParamTier.Security },

        // ── ops ───────────────────────────────────────────────────────────────
        new() { Key = "nfc_deadline_ms", SetCommand = "SET-NFC-DEADLINE", Default = 8000, Min = 3000, Max = 30000, Label = "NFC tap-session deadline, ms", Tier = ParamTier.Ops },
        new() { Key = "ble_idle_ms", SetCommand = "SET-BLE-IDLE", Default = 30000, Min = 5000, Max = 300000, Label = "BLE idle watchdog, ms", Tier = ParamTier.Ops },
        new() { Key = PassageDirKey, SetCommand = "SET-PASSAGE-DIRECTION", Default = 1, Min = 1, Max = 2, Label = "Passage direction", Tier = ParamTier.Ops, IsEnum = true, EnumLabels = PassageDirLabels },
        new() { Key = "wl_max", SetCommand = "SET-WHITELIST-MAX", Default = 256, Min = 32, Max = 2048, Label = "Max whitelist entries per packet", Tier = ParamTier.Ops },
        new() { Key = "bld_max", SetCommand = "SET-BL-DELTA-MAX", Default = 256, Min = 32, Max = 2048, Label = "Max blacklist-delta per packet", Tier = ParamTier.Ops },
        new() { Key = "cd_end", SetCommand = "SET-COOLDOWN-END", Default = 3000, Min = 500, Max = 10000, Label = "Cooldown after END, ms", Tier = ParamTier.Ops },
        new() { Key = "cd_grant", SetCommand = "SET-COOLDOWN-GRANT", Default = 4500, Min = 1000, Max = 15000, Label = "Cooldown after GRANT, ms", Tier = ParamTier.Ops },
        new() { Key = "bl_cap", SetCommand = "SET-BLACKLIST-CAP", Default = 256, Min = 16, Max = 256, Label = "Local blacklist cap (soft; max=compile size)", Tier = ParamTier.Ops },
        new() { Key = "nonce_ring", SetCommand = "SET-NONCE-RING", Default = 8, Min = 2, Max = 8, Label = "Nonce ring depth (soft; max=compile size)", Tier = ParamTier.Ops },
        // handover_required is a numeric CFG param (0/1) on the wire — emitted as
        // "SET-HANDOVER-REQUIRED <0|1>" — but rendered as a Switch for nicer UX.
        new() { Key = "handover_required", SetCommand = "SET-HANDOVER-REQUIRED", Default = 0, Min = 0, Max = 1, Label = "Require verified NFC→BLE handover", Tier = ParamTier.Ops, IsBool = true },

        // ── hardware (PN532 / BLE interop timings) ────────────────────────────
        new() { Key = "pn532_retries", SetCommand = "SET-PN532-RETRY", Default = 16, Min = 1, Max = 255, Label = "PN532 passive retries", Tier = ParamTier.Hardware },
        new() { Key = "rf_atr", SetCommand = "SET-RF-ATR", Default = 15, Min = 10, Max = 15, Label = "PN532 RF timing ATR", Tier = ParamTier.Hardware },
        new() { Key = "rf_retry", SetCommand = "SET-RF-RETRY", Default = 15, Min = 10, Max = 15, Label = "PN532 RF timing retry", Tier = ParamTier.Hardware },
        new() { Key = "field_pause", SetCommand = "SET-FIELD-PAUSE", Default = 80, Min = 50, Max = 500, Label = "Field reset pause, ms", Tier = ParamTier.Hardware },
        new() { Key = "push_retries", SetCommand = "SET-PUSH-RETRIES", Default = 3, Min = 1, Max = 10, Label = "PUSH_INFO retries", Tier = ParamTier.Hardware },
        new() { Key = "push_delay", SetCommand = "SET-PUSH-DELAY", Default = 40, Min = 10, Max = 500, Label = "PUSH_INFO retry delay, ms", Tier = ParamTier.Hardware },
        new() { Key = "reinit_thr", SetCommand = "SET-REINIT-THRESHOLD", Default = 3, Min = 1, Max = 20, Label = "PN532 fail-reinit threshold", Tier = ParamTier.Hardware },
        new() { Key = "nfc_detect", SetCommand = "SET-NFC-DETECT", Default = 100, Min = 20, Max = 500, Label = "NFC target detect timeout, ms", Tier = ParamTier.Hardware },
        new() { Key = "ble_mtu", SetCommand = "SET-BLE-MTU", Default = 247, Min = 243, Max = 517, Label = "BLE requested MTU", Tier = ParamTier.Hardware },
        new() { Key = "ble_info_defer", SetCommand = "SET-BLE-INFO-DEFER", Default = 50, Min = 10, Max = 500, Label = "Deferred BLE INFO-push, ms", Tier = ParamTier.Hardware },
    };

    private static readonly Dictionary<string, ParamSpec> ByKeyMap =
        All.ToDictionary(p => p.Key, StringComparer.Ordinal);

    public static ParamSpec ByKey(string key) => ByKeyMap[key];

    public static bool TryByKey(string key, out ParamSpec spec) => ByKeyMap.TryGetValue(key, out spec!);
}
