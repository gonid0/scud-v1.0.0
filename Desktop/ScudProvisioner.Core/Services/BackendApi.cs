using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using ScudProvisioner.Models;

namespace ScudProvisioner.Services;

public interface IBackendApi
{
    Task<IReadOnlyList<ReaderGroup>> ListReaderGroupsAsync(CancellationToken ct = default);
    Task<EnrollResponse> EnrollReaderAsync(EnrollRequest body, CancellationToken ct = default);
    // Phase 1: server reader-profiles + resolved provisioning script.
    Task<IReadOnlyList<ReaderProfileDto>> ListReaderProfilesAsync(CancellationToken ct = default);
    Task<ConfigScriptResponse> GetReaderConfigScriptAsync(string readerIdHex, CancellationToken ct = default);
}

public sealed class BackendApi : IBackendApi
{
    private readonly ISettingsService _settings;
    private readonly JsonSerializerOptions _json = new(JsonSerializerDefaults.Web);

    public BackendApi(ISettingsService settings)
    {
        _settings = settings;
    }

    private HttpClient NewClient()
    {
        var http = new HttpClient
        {
            BaseAddress = new Uri(NormalizeBase(_settings.BackendUrl)),
            Timeout = TimeSpan.FromSeconds(30),
        };
        http.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/json"));
        if (!string.IsNullOrWhiteSpace(_settings.AdminApiKey))
            http.DefaultRequestHeaders.Add("X-Api-Key", _settings.AdminApiKey);
        return http;
    }

    private static string NormalizeBase(string url)
    {
        if (string.IsNullOrWhiteSpace(url)) return "http://localhost:8000/";
        return url.EndsWith('/') ? url : url + "/";
    }

    public async Task<IReadOnlyList<ReaderGroup>> ListReaderGroupsAsync(CancellationToken ct = default)
    {
        using var http = NewClient();
        var groups = new List<ReaderGroup>();
        int cursor = 0;
        const int limit = 200;
        while (true)
        {
            using var resp = await http.GetAsync($"api/v1/admin/reader-groups?cursor={cursor}&limit={limit}", ct);
            await EnsureOk(resp, ct);
            var page = await resp.Content.ReadFromJsonAsync<ReaderGroupListResponse>(_json, ct)
                       ?? new ReaderGroupListResponse();
            if (page.Items.Count == 0) break;
            groups.AddRange(page.Items);
            if (page.Items.Count < limit) break;
            cursor += page.Items.Count;
        }
        return groups;
    }

    public async Task<EnrollResponse> EnrollReaderAsync(EnrollRequest body, CancellationToken ct = default)
    {
        using var http = NewClient();
        using var resp = await http.PostAsJsonAsync("api/v1/admin/readers/enroll", body, _json, ct);
        await EnsureOk(resp, ct);
        return await resp.Content.ReadFromJsonAsync<EnrollResponse>(_json, ct)
               ?? throw new InvalidOperationException("Empty enroll response");
    }

    public async Task<IReadOnlyList<ReaderProfileDto>> ListReaderProfilesAsync(CancellationToken ct = default)
    {
        using var http = NewClient();
        using var resp = await http.GetAsync("api/v1/admin/reader-profiles", ct);
        await EnsureOk(resp, ct);
        var page = await resp.Content.ReadFromJsonAsync<ReaderProfileListResponse>(_json, ct)
                   ?? new ReaderProfileListResponse();
        return page.Items;
    }

    public async Task<ConfigScriptResponse> GetReaderConfigScriptAsync(string readerIdHex, CancellationToken ct = default)
    {
        using var http = NewClient();
        using var resp = await http.GetAsync($"api/v1/admin/readers/{readerIdHex}/config-script", ct);
        await EnsureOk(resp, ct);
        return await resp.Content.ReadFromJsonAsync<ConfigScriptResponse>(_json, ct)
               ?? throw new InvalidOperationException("Empty config-script response");
    }

    private static async Task EnsureOk(HttpResponseMessage resp, CancellationToken ct)
    {
        if (resp.IsSuccessStatusCode) return;
        var body = await resp.Content.ReadAsStringAsync(ct);
        throw new HttpRequestException(
            $"HTTP {(int)resp.StatusCode} {resp.ReasonPhrase}: {body}");
    }
}
