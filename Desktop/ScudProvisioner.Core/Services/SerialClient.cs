using System.IO.Ports;
using System.Text;

namespace ScudProvisioner.Services;

public interface ISerialClient : IDisposable
{
    bool IsOpen { get; }
    void Open(string portName, int baud);
    void Close();
    Task<string> SendAsync(string command, TimeSpan? readWindow = null, CancellationToken ct = default);

    static IReadOnlyList<string> EnumeratePorts()
        => SerialPort.GetPortNames().Distinct().OrderBy(p => p).ToList();
}

public sealed class SerialClient : ISerialClient
{
    private SerialPort? _port;

    public bool IsOpen => _port?.IsOpen == true;

    public void Open(string portName, int baud)
    {
        Close();
        _port = new SerialPort(portName, baud, Parity.None, 8, StopBits.One)
        {
            ReadTimeout = 2000,
            WriteTimeout = 2000,
            NewLine = "\n",
            Encoding = Encoding.ASCII,
            DtrEnable = false,
            RtsEnable = false,
        };
        _port.Open();
        // Дать ESP32 успокоиться после reset (auto-DTR на некоторых USB-bridge'ах).
        Thread.Sleep(2000);
        _port.DiscardInBuffer();
        _port.DiscardOutBuffer();
    }

    public void Close()
    {
        try { _port?.Close(); } catch { /* swallow */ }
        _port?.Dispose();
        _port = null;
    }

    public async Task<string> SendAsync(string command, TimeSpan? readWindow = null, CancellationToken ct = default)
    {
        if (_port is null || !_port.IsOpen)
            throw new InvalidOperationException("Serial port is not open");

        var bytes = Encoding.ASCII.GetBytes(command + "\r\n");
        _port.DiscardInBuffer();
        await _port.BaseStream.WriteAsync(bytes, ct);
        await _port.BaseStream.FlushAsync(ct);

        var window = readWindow ?? TimeSpan.FromMilliseconds(400);
        var deadline = DateTime.UtcNow + window;
        var sb = new StringBuilder();
        var buffer = new byte[512];
        while (DateTime.UtcNow < deadline)
        {
            if (_port.BytesToRead > 0)
            {
                int n = _port.Read(buffer, 0, Math.Min(buffer.Length, _port.BytesToRead));
                sb.Append(Encoding.ASCII.GetString(buffer, 0, n));
                deadline = DateTime.UtcNow + TimeSpan.FromMilliseconds(120); // продлеваем пока есть поток
            }
            else
            {
                await Task.Delay(20, ct);
            }
        }
        return sb.ToString();
    }

    public void Dispose() => Close();
}
