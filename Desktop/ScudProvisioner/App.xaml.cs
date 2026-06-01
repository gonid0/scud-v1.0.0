namespace ScudProvisioner;

public partial class App : Application
{
    public App()
    {
        InitializeComponent();
    }

    protected override Window CreateWindow(IActivationState? activationState)
    {
        var window = new Window(new AppShell())
        {
            Title = "SCUD Reader Provisioner",
            Width = 980,
            Height = 720,
            MinimumWidth = 720,
            MinimumHeight = 560,
        };
        return window;
    }
}
