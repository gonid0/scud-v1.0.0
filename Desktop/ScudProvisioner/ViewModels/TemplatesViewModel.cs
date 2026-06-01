using System.Collections.ObjectModel;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using ScudProvisioner.Models;
using ScudProvisioner.Services;

namespace ScudProvisioner.ViewModels;

/// <summary>
/// Editor for the config-template library (docs/11 §4). Operator picks a template,
/// edits every param grouped by tier (Entry/Stepper, Switch, enum Picker), with
/// client-side clamping, and can Save / Save As… / Delete / Set active.
/// </summary>
public partial class TemplatesViewModel : ObservableObject
{
    private readonly IConfigTemplateService _service;

    public ObservableCollection<DeviceConfigTemplate> Templates { get; } = new();
    public ObservableCollection<ParamRowViewModel> SecurityParams { get; } = new();
    public ObservableCollection<ParamRowViewModel> OpsParams { get; } = new();
    public ObservableCollection<ParamRowViewModel> HardwareParams { get; } = new();

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(IsBuiltInSelected))]
    [NotifyCanExecuteChangedFor(nameof(DeleteCommand))]
    private DeviceConfigTemplate? selectedTemplate;

    [ObservableProperty] private string activeName = "";
    [ObservableProperty] private string deviceType = "";
    [ObservableProperty] private string newName = "";
    [ObservableProperty] private string status = "";

    public bool IsBuiltInSelected =>
        SelectedTemplate is not null &&
        string.Equals(SelectedTemplate.Name, DeviceConfigTemplate.BuiltInDefaultName, StringComparison.Ordinal);

    public TemplatesViewModel(IConfigTemplateService service)
    {
        _service = service;
        Reload();
    }

    public void Reload()
    {
        var previouslySelected = SelectedTemplate?.Name;
        Templates.Clear();
        foreach (var t in _service.List())
            Templates.Add(t);

        ActiveName = _service.ActiveName;

        var pick = Templates.FirstOrDefault(t => string.Equals(t.Name, previouslySelected, StringComparison.Ordinal))
                   ?? Templates.FirstOrDefault(t => string.Equals(t.Name, ActiveName, StringComparison.Ordinal))
                   ?? Templates.FirstOrDefault();
        SelectedTemplate = pick;
        Status = "";
    }

    partial void OnSelectedTemplateChanged(DeviceConfigTemplate? value)
    {
        LoadRowsFrom(value);
        DeviceType = value?.DeviceType ?? "";
        NewName = value?.Name ?? "";
    }

    private void LoadRowsFrom(DeviceConfigTemplate? template)
    {
        SecurityParams.Clear();
        OpsParams.Clear();
        HardwareParams.Clear();
        if (template is null) return;

        foreach (var spec in ConfigParamCatalog.All)
        {
            var row = new ParamRowViewModel(spec, template.GetValue(spec));
            switch (spec.Tier)
            {
                case ParamTier.Security: SecurityParams.Add(row); break;
                case ParamTier.Ops: OpsParams.Add(row); break;
                case ParamTier.Hardware: HardwareParams.Add(row); break;
            }
        }
    }

    private DeviceConfigTemplate BuildFromRows(string name)
    {
        var t = new DeviceConfigTemplate { Name = name, DeviceType = (DeviceType ?? "").Trim() };
        foreach (var row in SecurityParams.Concat(OpsParams).Concat(HardwareParams))
            t.Values[row.Key] = row.CurrentValue();
        return t;
    }

    private void NormalizeAll()
    {
        foreach (var row in SecurityParams.Concat(OpsParams).Concat(HardwareParams))
            row.NormalizeText();
    }

    [RelayCommand]
    private void Save()
    {
        if (SelectedTemplate is null) { Status = "No template selected."; return; }
        NormalizeAll();
        var saved = BuildFromRows(SelectedTemplate.Name);
        _service.Save(saved);
        var keep = saved.Name;
        Reload();
        SelectedTemplate = Templates.FirstOrDefault(t => string.Equals(t.Name, keep, StringComparison.Ordinal));
        Status = $"Saved \"{keep}\".";
    }

    [RelayCommand]
    private void SaveAs()
    {
        var name = (NewName ?? "").Trim();
        if (string.IsNullOrWhiteSpace(name)) { Status = "Enter a name for Save As."; return; }
        NormalizeAll();
        var saved = BuildFromRows(name);
        _service.Save(saved);
        Reload();
        SelectedTemplate = Templates.FirstOrDefault(t => string.Equals(t.Name, name, StringComparison.Ordinal));
        Status = $"Saved as \"{name}\".";
    }

    [RelayCommand(CanExecute = nameof(CanDelete))]
    private void Delete()
    {
        if (SelectedTemplate is null) return;
        var name = SelectedTemplate.Name;
        if (_service.Delete(name))
        {
            Reload();
            Status = $"Deleted \"{name}\".";
        }
        else
        {
            Status = "The built-in default template cannot be deleted.";
        }
    }

    private bool CanDelete() => SelectedTemplate is not null && !IsBuiltInSelected;

    [RelayCommand]
    private void SetActive()
    {
        if (SelectedTemplate is null) { Status = "No template selected."; return; }
        _service.SetActive(SelectedTemplate.Name);
        ActiveName = _service.ActiveName;
        Status = $"Active template = \"{ActiveName}\".";
    }
}
