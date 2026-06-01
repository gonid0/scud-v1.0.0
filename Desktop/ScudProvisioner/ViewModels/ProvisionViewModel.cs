using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using ScudProvisioner.Models;
using ScudProvisioner.Services;

namespace ScudProvisioner.ViewModels;

public partial class ProvisionViewModel : ObservableObject
{
    private readonly IBackendApi _api;
    private readonly ProvisionFlow _flow;

    public ObservableCollection<string> Ports { get; } = new();
    public ObservableCollection<ReaderGroup> Groups { get; } = new();
    public ObservableCollection<ReaderProfileDto> Profiles { get; } = new();
    public ObservableCollection<string> LogLines { get; } = new();

    [ObservableProperty] private string? selectedPort;
    [ObservableProperty] private ReaderGroup? selectedGroup;
    // Phase 1: null = use the local device-config template (Phase 4 default).
    [ObservableProperty] private ReaderProfileDto? selectedProfile;
    [ObservableProperty] private string displayName = "";
    [ObservableProperty] private string description = "";

    [ObservableProperty]
    [NotifyCanExecuteChangedFor(nameof(ProvisionCommand))]
    [NotifyCanExecuteChangedFor(nameof(RefreshGroupsCommand))]
    [NotifyCanExecuteChangedFor(nameof(RefreshProfilesCommand))]
    [NotifyCanExecuteChangedFor(nameof(RefreshPortsCommand))]
    private bool isBusy;

    [ObservableProperty] private string statusBanner = "";

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(HasLastResult))]
    private string? lastReaderIdHex;
    [ObservableProperty] private string? lastServerEdHex;
    [ObservableProperty] private string? lastServerXHex;

    public bool HasLastResult => !string.IsNullOrEmpty(LastReaderIdHex);

    public ProvisionViewModel(IBackendApi api, ProvisionFlow flow)
    {
        _api = api;
        _flow = flow;
        RefreshPorts();
    }

    [RelayCommand(CanExecute = nameof(NotBusy))]
    private void RefreshPorts()
    {
        Ports.Clear();
        foreach (var p in ISerialClient.EnumeratePorts())
            Ports.Add(p);
        if (SelectedPort is null || !Ports.Contains(SelectedPort))
            SelectedPort = Ports.FirstOrDefault();
    }

    [RelayCommand(CanExecute = nameof(NotBusy))]
    private async Task RefreshGroupsAsync()
    {
        IsBusy = true;
        StatusBanner = "Loading reader groups...";
        try
        {
            Groups.Clear();
            var groups = await _api.ListReaderGroupsAsync();
            foreach (var g in groups) Groups.Add(g);
            if (SelectedGroup is null && Groups.Count > 0) SelectedGroup = Groups[0];
            StatusBanner = $"Loaded {Groups.Count} reader group(s).";
        }
        catch (Exception ex)
        {
            StatusBanner = $"Failed to load groups: {ex.Message}";
        }
        finally { IsBusy = false; }
    }

    [RelayCommand(CanExecute = nameof(NotBusy))]
    private async Task RefreshProfilesAsync()
    {
        IsBusy = true;
        StatusBanner = "Loading reader profiles...";
        try
        {
            Profiles.Clear();
            var profiles = await _api.ListReaderProfilesAsync();
            foreach (var p in profiles) Profiles.Add(p);
            // Leave SelectedProfile unset on purpose — an empty selection means
            // "use the local template" (the backward-compatible default).
            StatusBanner = $"Loaded {Profiles.Count} profile(s). Leave empty to use the local template.";
        }
        catch (Exception ex)
        {
            // Non-fatal: the local-template path still works without server profiles.
            StatusBanner = $"Failed to load profiles: {ex.Message}";
        }
        finally { IsBusy = false; }
    }

    [RelayCommand(CanExecute = nameof(CanProvision))]
    private async Task ProvisionAsync()
    {
        IsBusy = true;
        LogLines.Clear();
        StatusBanner = "Provisioning...";
        var progress = new Progress<string>(line =>
        {
            LogLines.Add(line);
            if (LogLines.Count > 500)
                LogLines.RemoveAt(0);
        });

        try
        {
            var result = await _flow.RunAsync(new ProvisionInput
            {
                PortName = SelectedPort!,
                Group = SelectedGroup!,
                DisplayName = DisplayName.Trim(),
                Description = Description?.Trim(),
                Profile = SelectedProfile,
                HardwareClass = SelectedProfile?.HardwareClass,
            }, progress);

            LastReaderIdHex = result.ReaderIdHex;
            LastServerEdHex = result.ServerEdPubkeyHex;
            LastServerXHex = result.ServerXPubkeyHex;
            StatusBanner = $"Provisioned reader {result.ReaderIdHex}. Press RST on the device.";
        }
        catch (Exception ex)
        {
            LogLines.Add($"ERROR: {ex.Message}");
            StatusBanner = $"Failed: {ex.Message}";
        }
        finally { IsBusy = false; }
    }

    private bool NotBusy => !IsBusy;

    private bool CanProvision()
        => !IsBusy
           && !string.IsNullOrWhiteSpace(SelectedPort)
           && SelectedGroup is not null
           && !string.IsNullOrWhiteSpace(DisplayName);

    partial void OnSelectedPortChanged(string? value) => ProvisionCommand.NotifyCanExecuteChanged();
    partial void OnSelectedGroupChanged(ReaderGroup? value) => ProvisionCommand.NotifyCanExecuteChanged();
    partial void OnDisplayNameChanged(string value) => ProvisionCommand.NotifyCanExecuteChanged();
}
