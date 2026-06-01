using Microsoft.Extensions.Logging;
using ScudProvisioner.Platform;
using ScudProvisioner.Services;
using ScudProvisioner.ViewModels;
using ScudProvisioner.Views;

namespace ScudProvisioner;

public static class MauiProgram
{
    public static MauiApp CreateMauiApp()
    {
        var builder = MauiApp.CreateBuilder();
        builder.UseMauiApp<App>();

        builder.Services.AddSingleton<IKeyValueStore, MauiKeyValueStore>();
        builder.Services.AddSingleton<ISettingsService, SettingsService>();
        builder.Services.AddSingleton<IConfigTemplateService, ConfigTemplateService>();
        builder.Services.AddSingleton<IBackendApi, BackendApi>();
        builder.Services.AddTransient<ISerialClient, SerialClient>();
        builder.Services.AddTransient<ProvisionFlow>();

        builder.Services.AddSingleton<SettingsViewModel>();
        builder.Services.AddSingleton<ProvisionViewModel>();
        builder.Services.AddSingleton<TemplatesViewModel>();
        builder.Services.AddSingleton<SettingsPage>();
        builder.Services.AddSingleton<ProvisionPage>();
        builder.Services.AddSingleton<TemplatesPage>();

#if DEBUG
        builder.Logging.AddDebug();
#endif

        return builder.Build();
    }
}
