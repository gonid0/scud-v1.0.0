using ScudProvisioner.Services;

namespace ScudProvisioner.Platform;

/// <summary>
/// MAUI-backed <see cref="IKeyValueStore"/> so the GUI keeps its existing storage
/// behaviour: scalar settings live in <c>Preferences.Default</c> and the template
/// JSON lives under <c>FileSystem.AppDataDirectory</c> (exactly as before the Core
/// split). The cross-platform CLI uses <see cref="FileKeyValueStore"/> instead.
/// </summary>
public sealed class MauiKeyValueStore : IKeyValueStore
{
    public string AppDataDir => FileSystem.AppDataDirectory;

    public string GetString(string k, string def) => Preferences.Default.Get(k, def);
    public void SetString(string k, string v) => Preferences.Default.Set(k, v);
    public int GetInt(string k, int def) => Preferences.Default.Get(k, def);
    public void SetInt(string k, int v) => Preferences.Default.Set(k, v);
}
