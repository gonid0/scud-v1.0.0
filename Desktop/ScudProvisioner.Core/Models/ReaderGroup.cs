using System.Text.Json.Serialization;

namespace ScudProvisioner.Models;

public sealed class ReaderGroup
{
    [JsonPropertyName("group_id")]
    public string GroupId { get; init; } = "";

    [JsonPropertyName("name")]
    public string Name { get; init; } = "";

    [JsonPropertyName("description")]
    public string? Description { get; init; }

    public override string ToString()
        => string.IsNullOrEmpty(Description) ? Name : $"{Name} — {Description}";
}

public sealed class ReaderGroupListResponse
{
    [JsonPropertyName("items")]
    public List<ReaderGroup> Items { get; init; } = new();
}
