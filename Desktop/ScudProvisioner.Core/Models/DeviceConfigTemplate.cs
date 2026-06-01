using System.Text.Json.Serialization;

namespace ScudProvisioner.Models;

/// <summary>
/// A named, persisted reader-config template. <see cref="Values"/> is keyed by
/// <see cref="ParamSpec.Key"/>; any param missing from the dictionary falls back
/// to its catalog <see cref="ParamSpec.Default"/> via <see cref="GetValue"/>.
/// </summary>
public sealed class DeviceConfigTemplate
{
    [JsonPropertyName("name")]
    public string Name { get; set; } = "";

    [JsonPropertyName("device_type")]
    public string DeviceType { get; set; } = "";

    [JsonPropertyName("values")]
    public Dictionary<string, long> Values { get; set; } = new();

    /// <summary>Effective (clamped) value for a param: stored value if present, else the catalog default.</summary>
    public long GetValue(ParamSpec spec)
    {
        var raw = Values.TryGetValue(spec.Key, out var v) ? v : spec.Default;
        return spec.Clamp(raw);
    }

    public void SetValue(ParamSpec spec, long value) => Values[spec.Key] = spec.Clamp(value);

    public DeviceConfigTemplate DeepCopy() => new()
    {
        Name = Name,
        DeviceType = DeviceType,
        Values = new Dictionary<string, long>(Values),
    };

    /// <summary>Built-in default template seeded on first run: every catalog param at its firmware default.</summary>
    public static DeviceConfigTemplate BuiltInDefault()
    {
        var t = new DeviceConfigTemplate
        {
            Name = BuiltInDefaultName,
            DeviceType = "door_mains",
        };
        foreach (var spec in ConfigParamCatalog.All)
            t.Values[spec.Key] = spec.Default;
        return t;
    }

    public const string BuiltInDefaultName = "ESP32 base (door_mains)";
}
