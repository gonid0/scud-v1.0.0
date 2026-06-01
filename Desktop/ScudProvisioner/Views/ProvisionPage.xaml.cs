using System.Collections.Specialized;
using ScudProvisioner.ViewModels;

namespace ScudProvisioner.Views;

public partial class ProvisionPage : ContentPage
{
    private readonly ProvisionViewModel _vm;

    public ProvisionPage(ProvisionViewModel vm)
    {
        InitializeComponent();
        BindingContext = _vm = vm;
        _vm.LogLines.CollectionChanged += OnLogChanged;
    }

    protected override void OnDisappearing()
    {
        base.OnDisappearing();
    }

    private void OnLogChanged(object? sender, NotifyCollectionChangedEventArgs e)
    {
        // No-op for now — CollectionView in MAUI auto-scrolls when items added is unreliable.
        // We could call ScrollTo on the CollectionView here once it's named in XAML.
    }
}
