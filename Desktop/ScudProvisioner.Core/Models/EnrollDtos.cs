using System.Text.Json.Serialization;

namespace ScudProvisioner.Models;

public sealed class EnrollRequest
{
    [JsonPropertyName("reader_id")]
    public string ReaderIdHex { get; init; } = "";

    [JsonPropertyName("reader_pubkey")]
    public string ReaderPubkeyBase64 { get; init; } = "";

    [JsonPropertyName("reader_group_id")]
    public string ReaderGroupId { get; init; } = "";

    [JsonPropertyName("display_name")]
    public string DisplayName { get; init; } = "";

    [JsonPropertyName("description")]
    public string? Description { get; init; }

    // Phase 1 (server parameterization) — all optional. When null they are NOT
    // serialized, keeping an identity-only enroll byte-compatible with before.
    [JsonPropertyName("profile_id")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? ProfileId { get; init; }

    [JsonPropertyName("hardware_class")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? HardwareClass { get; init; }

    [JsonPropertyName("overrides")]
    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public Dictionary<string, int>? Overrides { get; init; }
}

public sealed class EnrollResponse
{
    [JsonPropertyName("reader_id")]
    public string ReaderIdHex { get; init; } = "";

    [JsonPropertyName("server_ed25519_pubkey")]
    public string ServerEdPubkeyBase64 { get; init; } = "";

    [JsonPropertyName("server_x25519_pubkey")]
    public string ServerXPubkeyBase64 { get; init; } = "";

    [JsonPropertyName("reader_group_id")]
    public string ReaderGroupId { get; init; } = "";
}

// ---------------------------------------------------------------------------
// Phase 1 — server reader-profiles + resolved config-script
// ---------------------------------------------------------------------------

public sealed class ReaderProfileDto
{
    [JsonPropertyName("profile_id")]
    public string ProfileId { get; init; } = "";

    [JsonPropertyName("name")]
    public string Name { get; init; } = "";

    [JsonPropertyName("hardware_class")]
    public string HardwareClass { get; init; } = "";

    [JsonPropertyName("description")]
    public string? Description { get; init; }

    [JsonPropertyName("params")]
    public Dictionary<string, long> Params { get; init; } = new();
}

public sealed class ReaderProfileListResponse
{
    [JsonPropertyName("items")]
    public List<ReaderProfileDto> Items { get; init; } = new();
}

// One resolved SET-* line. arg == null => a verb command sent alone
// (e.g. BLE-ENABLE / BLE-DISABLE); arg != null => "<command> <arg>".
public sealed class ConfigScriptLine
{
    [JsonPropertyName("command")]
    public string Command { get; init; } = "";

    [JsonPropertyName("arg")]
    public string? Arg { get; init; }
}

public sealed class ConfigScriptResponse
{
    [JsonPropertyName("reader_id")]
    public string ReaderIdHex { get; init; } = "";

    [JsonPropertyName("hardware_class")]
    public string HardwareClass { get; init; } = "";

    [JsonPropertyName("profile_id")]
    public string? ProfileId { get; init; }

    [JsonPropertyName("params")]
    public Dictionary<string, long> Params { get; init; } = new();

    [JsonPropertyName("script")]
    public List<ConfigScriptLine> Script { get; init; } = new();

    [JsonPropertyName("warnings")]
    public List<string> Warnings { get; init; } = new();
}
