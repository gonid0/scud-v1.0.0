namespace ScudProvisioner.Services;

public interface ISettingsService
{
    string BackendUrl { get; set; }
    string AdminApiKey { get; set; }
    int LockDurationMs { get; set; }
    int Baud { get; set; }
}

public sealed class SettingsService : ISettingsService
{
    private const string KeyBackendUrl     = "scud.backend_url";
    private const string KeyAdminApiKey    = "scud.admin_api_key";
    private const string KeyLockDurationMs = "scud.lock_duration_ms";
    private const string KeyBaud           = "scud.serial_baud";

    private readonly IKeyValueStore _store;

    public SettingsService(IKeyValueStore store)
    {
        _store = store;
    }

    public string BackendUrl
    {
        get => _store.GetString(KeyBackendUrl, "http://localhost:8000");
        set => _store.SetString(KeyBackendUrl, value);
    }

    public string AdminApiKey
    {
        get => _store.GetString(KeyAdminApiKey, "");
        set => _store.SetString(KeyAdminApiKey, value);
    }

    public int LockDurationMs
    {
        get => _store.GetInt(KeyLockDurationMs, 3000);
        set => _store.SetInt(KeyLockDurationMs, value);
    }

    public int Baud
    {
        get => _store.GetInt(KeyBaud, 115200);
        set => _store.SetInt(KeyBaud, value);
    }
}
