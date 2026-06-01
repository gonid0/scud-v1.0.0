using ScudProvisioner.ViewModels;

namespace ScudProvisioner.Views;

public partial class TemplatesPage : ContentPage
{
    private readonly TemplatesViewModel _vm;

    public TemplatesPage(TemplatesViewModel vm)
    {
        InitializeComponent();
        BindingContext = _vm = vm;
    }

    protected override void OnAppearing()
    {
        base.OnAppearing();
        _vm.Reload();
    }
}
