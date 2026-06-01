using System.Text.Json;

namespace ScudProvisioner.Services;

/// <summary>
/// Minimal key/value persistence abstraction so the Core service layer no longer
/// depends on MAUI's <c>Preferences.Default</c> / <c>FileSystem.AppDataDirectory</c>.
/// The MAUI GUI supplies a Preferences-backed implementation; the console CLI uses
/// the portable <see cref="FileKeyValueStore"/>.
/// </summary>
public interface IKeyValueStore
{
    string GetString(string k, string def);
    void SetString(string k, string v);
    int GetInt(string k, int def);
    void SetInt(string k, int v);

    /// <summary>App data directory both implementations use for file-backed state (e.g. templates).</summary>
    string AppDataDir { get; }
}

/// <summary>
/// Portable default <see cref="IKeyValueStore"/>: a JSON dictionary persisted under
/// <c>%APPDATA%/ScudProvisioner</c> (Environment.SpecialFolder.ApplicationData).
/// The directory is created on first use; <see cref="AppDataDir"/> returns it.
/// Used by the cross-platform CLI (and anything else without MAUI present).
/// </summary>
public sealed class FileKeyValueStore : IKeyValueStore
{
    private const string PrefsFileName = "preferences.json";

    private static readonly JsonSerializerOptions JsonOpts =
        new(JsonSerializerDefaults.Web) { WriteIndented = true };

    private readonly string _prefsPath;
    private readonly object _gate = new();
    private Dictionary<string, string> _values;

    public string AppDataDir { get; }

    public FileKeyValueStore()
    {
        AppDataDir = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "ScudProvisioner");
        Directory.CreateDirectory(AppDataDir);
        _prefsPath = Path.Combine(AppDataDir, PrefsFileName);
        _values = Load();
    }

    private Dictionary<string, string> Load()
    {
        try
        {
            if (File.Exists(_prefsPath))
            {
                var json = File.ReadAllText(_prefsPath);
                var dict = JsonSerializer.Deserialize<Dictionary<string, string>>(json, JsonOpts);
                if (dict is not null) return dict;
            }
        }
        catch
        {
            // Corrupt/unreadable store → start fresh rather than crash.
        }
        return new Dictionary<string, string>(StringComparer.Ordinal);
    }

    private void Persist()
    {
        var json = JsonSerializer.Serialize(_values, JsonOpts);
        File.WriteAllText(_prefsPath, json);
    }

    public string GetString(string k, string def)
    {
        lock (_gate)
            return _values.TryGetValue(k, out var v) ? v : def;
    }

    public void SetString(string k, string v)
    {
        lock (_gate)
        {
            _values[k] = v;
            Persist();
        }
    }

    public int GetInt(string k, int def)
    {
        lock (_gate)
            return _values.TryGetValue(k, out var v) && int.TryParse(v, out var i) ? i : def;
    }

    public void SetInt(string k, int v)
    {
        lock (_gate)
        {
            _values[k] = v.ToString();
            Persist();
        }
    }
}
