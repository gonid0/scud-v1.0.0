using System.Text.Json;
using ScudProvisioner.Models;

namespace ScudProvisioner.Services;

/// <summary>
/// Persists a list of named <see cref="DeviceConfigTemplate"/> as JSON in
/// <c>{IKeyValueStore.AppDataDir}/config_templates.json</c>. The active-template
/// name is persisted separately via <see cref="IKeyValueStore"/>. The built-in
/// default template is seeded on first run and is undeletable.
/// </summary>
public interface IConfigTemplateService
{
    IReadOnlyList<DeviceConfigTemplate> List();
    DeviceConfigTemplate? Get(string name);

    /// <summary>Insert or overwrite the template under <paramref name="template"/>.Name.</summary>
    void Save(DeviceConfigTemplate template);

    /// <summary>Delete by name. The built-in default cannot be deleted (returns false).</summary>
    bool Delete(string name);

    string ActiveName { get; }
    DeviceConfigTemplate GetActive();
    void SetActive(string name);
}

public sealed class ConfigTemplateService : IConfigTemplateService
{
    private const string FileName = "config_templates.json";
    private const string KeyActiveName = "scud.active_template";

    private static readonly JsonSerializerOptions JsonOpts =
        new(JsonSerializerDefaults.Web) { WriteIndented = true };

    private readonly IKeyValueStore _store;
    private readonly string _path;
    private readonly object _gate = new();
    private List<DeviceConfigTemplate> _templates;

    public ConfigTemplateService(IKeyValueStore store)
    {
        _store = store;
        _path = Path.Combine(store.AppDataDir, FileName);
        _templates = Load();
        EnsureBuiltInSeeded();
    }

    private List<DeviceConfigTemplate> Load()
    {
        try
        {
            if (File.Exists(_path))
            {
                var json = File.ReadAllText(_path);
                var list = JsonSerializer.Deserialize<List<DeviceConfigTemplate>>(json, JsonOpts);
                if (list is not null)
                    return list.Where(t => !string.IsNullOrWhiteSpace(t.Name)).ToList();
            }
        }
        catch
        {
            // Corrupt/unreadable store → fall back to a fresh seeded list rather than crash.
        }
        return new List<DeviceConfigTemplate>();
    }

    private void Persist()
    {
        var json = JsonSerializer.Serialize(_templates, JsonOpts);
        File.WriteAllText(_path, json);
    }

    private void EnsureBuiltInSeeded()
    {
        lock (_gate)
        {
            if (!_templates.Any(t => string.Equals(t.Name, DeviceConfigTemplate.BuiltInDefaultName, StringComparison.Ordinal)))
            {
                _templates.Insert(0, DeviceConfigTemplate.BuiltInDefault());
                Persist();
            }
        }
    }

    public IReadOnlyList<DeviceConfigTemplate> List()
    {
        lock (_gate)
            return _templates.Select(t => t.DeepCopy()).ToList();
    }

    public DeviceConfigTemplate? Get(string name)
    {
        lock (_gate)
            return _templates.FirstOrDefault(t => string.Equals(t.Name, name, StringComparison.Ordinal))?.DeepCopy();
    }

    public void Save(DeviceConfigTemplate template)
    {
        if (string.IsNullOrWhiteSpace(template.Name))
            throw new ArgumentException("Template name is required.", nameof(template));

        lock (_gate)
        {
            var copy = template.DeepCopy();
            var idx = _templates.FindIndex(t => string.Equals(t.Name, copy.Name, StringComparison.Ordinal));
            if (idx >= 0) _templates[idx] = copy;
            else _templates.Add(copy);
            Persist();
        }
    }

    public bool Delete(string name)
    {
        lock (_gate)
        {
            if (string.Equals(name, DeviceConfigTemplate.BuiltInDefaultName, StringComparison.Ordinal))
                return false; // built-in is undeletable

            var idx = _templates.FindIndex(t => string.Equals(t.Name, name, StringComparison.Ordinal));
            if (idx < 0) return false;
            _templates.RemoveAt(idx);
            Persist();

            if (string.Equals(ActiveName, name, StringComparison.Ordinal))
                SetActive(DeviceConfigTemplate.BuiltInDefaultName);
            return true;
        }
    }

    public string ActiveName
    {
        get => _store.GetString(KeyActiveName, DeviceConfigTemplate.BuiltInDefaultName);
        private set => _store.SetString(KeyActiveName, value);
    }

    public DeviceConfigTemplate GetActive()
    {
        lock (_gate)
        {
            return Get(ActiveName)
                   ?? Get(DeviceConfigTemplate.BuiltInDefaultName)
                   ?? DeviceConfigTemplate.BuiltInDefault();
        }
    }

    public void SetActive(string name)
    {
        lock (_gate)
        {
            if (_templates.Any(t => string.Equals(t.Name, name, StringComparison.Ordinal)))
                ActiveName = name;
        }
    }
}
