using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using ScudProvisioner.Services;

namespace ScudProvisioner.ViewModels;

public partial class SettingsViewModel : ObservableObject
{
    private readonly ISettingsService _settings;

    [ObservableProperty] private string backendUrl = "";
    [ObservableProperty] private string adminApiKey = "";
    [ObservableProperty] private int lockDurationMs;
    [ObservableProperty] private int baud;
    [ObservableProperty] private string status = "";

    public SettingsViewModel(ISettingsService settings)
    {
        _settings = settings;
        Reload();
    }

    public void Reload()
    {
        BackendUrl = _settings.BackendUrl;
        AdminApiKey = _settings.AdminApiKey;
        LockDurationMs = _settings.LockDurationMs;
        Baud = _settings.Baud;
        Status = "";
    }

    [RelayCommand]
    private void Save()
    {
        _settings.BackendUrl = (BackendUrl ?? "").Trim();
        _settings.AdminApiKey = (AdminApiKey ?? "").Trim();
        _settings.LockDurationMs = LockDurationMs <= 0 ? 3000 : LockDurationMs;
        _settings.Baud = Baud <= 0 ? 115200 : Baud;
        Status = "Saved.";
    }
}
