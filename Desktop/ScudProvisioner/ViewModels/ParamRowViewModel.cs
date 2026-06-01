using CommunityToolkit.Mvvm.ComponentModel;
using ScudProvisioner.Models;

namespace ScudProvisioner.ViewModels;

/// <summary>
/// One editable row in the template editor, wrapping a <see cref="ParamSpec"/>.
/// Numeric params bind <see cref="ValueText"/> (clamped to [Min..Max] on commit);
/// bool params bind <see cref="BoolValue"/>; the passage_dir enum binds
/// <see cref="EnumOptions"/> / <see cref="SelectedEnum"/>.
/// </summary>
public partial class ParamRowViewModel : ObservableObject
{
    public ParamSpec Spec { get; }

    public string Label => Spec.Label;
    public string Key => Spec.Key;
    public string RangeHint => Spec.IsBool ? "boolean" : $"range {Spec.RangeHint} · default {Spec.Default}";
    public bool IsBool => Spec.IsBool;
    public bool IsEnum => Spec.IsEnum;
    public bool IsNumeric => !Spec.IsBool && !Spec.IsEnum;

    [ObservableProperty] private string valueText = "";
    [ObservableProperty] private bool boolValue;

    public IReadOnlyList<EnumOption> EnumOptions { get; }

    [ObservableProperty] private EnumOption? selectedEnum;

    public ParamRowViewModel(ParamSpec spec, long initialValue)
    {
        Spec = spec;
        var clamped = spec.Clamp(initialValue);

        EnumOptions = spec.IsEnum && spec.EnumLabels is not null
            ? spec.EnumLabels.Select(kv => new EnumOption(kv.Key, kv.Value)).ToList()
            : Array.Empty<EnumOption>();

        SetFromValue(clamped);
    }

    private void SetFromValue(long value)
    {
        BoolValue = value != 0;
        ValueText = value.ToString();
        SelectedEnum = EnumOptions.FirstOrDefault(o => o.Value == value) ?? EnumOptions.FirstOrDefault();
    }

    /// <summary>Current edited value, clamped to the spec range. Invalid numeric text falls back to the default.</summary>
    public long CurrentValue()
    {
        if (Spec.IsBool)
            return BoolValue ? 1 : 0;
        if (Spec.IsEnum)
            return SelectedEnum?.Value ?? Spec.Default;
        if (!long.TryParse(ValueText, out var v))
            v = Spec.Default;
        return Spec.Clamp(v);
    }

    /// <summary>Re-clamp the numeric text box to the spec range (called on Save / focus-out).</summary>
    public void NormalizeText()
    {
        if (Spec.IsBool || Spec.IsEnum) return;
        ValueText = CurrentValue().ToString();
    }
}

public sealed record EnumOption(long Value, string Label)
{
    public override string ToString() => Label;
}
