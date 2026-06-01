# SCUD Reader Provisioner

Desktop-приложение (.NET MAUI, Windows) для регистрации новых ESP32-ридеров SCUD.

Полностью повторяет логику `ESP32/firmware/tools/provisioner.py`, но в GUI:
выбор COM-порта, выбор reader-группы из бэкенда, ввод display name —
а дальше один клик «Provision reader» делает всё, что раньше делал CLI.

## Что делает приложение

1. Открывает COM-порт ESP32 (USB-UART), настройки 115200 8N1.
2. Шлёт `GEN-KEYPAIR`, `SHOW-PUBKEY` → получает Ed25519 pubkey ридера.
3. Генерирует случайный 16-байтный `reader_id`.
4. `POST /api/v1/admin/readers/enroll` на бэкенд (`X-Api-Key` из настроек) —
   получает `server_ed25519_pubkey` и `server_x25519_pubkey`.
5. Шлёт на ридер `SET-READER-ID`, `SET-GROUP-ID`, `SET-SERVER-ED-PUB`,
   `SET-SERVER-X-PUB`, `SET-LOCK-DURATION`, `SET-TIME` и финальный `COMMIT`.
6. Показывает в UI `reader_id` и серверные ключи (на случай ручной сверки)
   и просит нажать RST на плате.

## Сборка

Однократно установить MAUI workload:

```pwsh
dotnet workload install maui
```

Сборка и запуск (Windows):

```pwsh
cd Desktop/ScudProvisioner
dotnet restore
dotnet build -f net9.0-windows10.0.19041.0
dotnet run   -f net9.0-windows10.0.19041.0
```

## Использование

1. **Settings** → ввести `Backend base URL` и `Admin API key` (выдаётся в
   `/admin/api-keys`). Сохранить.
2. **Provision** → нажать `Load`, чтобы подтянуть список reader-групп.
3. Подключить ESP32 по USB → нажать ↻ рядом с Serial port.
4. Заполнить `Display name` (опционально description) → `Provision reader`.
5. Дождаться строки `Provisioning complete. Press the RST button` в логе.

## Файлы

- `Services/BackendApi.cs` — HTTP-клиент к `/admin/reader-groups` и
  `/admin/readers/enroll`.
- `Services/SerialClient.cs` — обёртка над `System.IO.Ports.SerialPort`.
- `Services/ProvisionFlow.cs` — оркестратор: GEN-KEYPAIR → enroll →
  SET-* → COMMIT.
- `ViewModels/ProvisionViewModel.cs`, `SettingsViewModel.cs` — MVVM.
- `Views/ProvisionPage.xaml`, `SettingsPage.xaml` — UI.

## Требования

- Windows 10 1809+ (10.0.17763) или новее.
- .NET 9 SDK + MAUI workload.
- Пользователь с правами на serial-порт ESP32 (обычно достаточно
  установленного USB-UART драйвера: CP210x / CH340 / FTDI).
- Admin API key с правом на `/admin/readers/enroll`.
