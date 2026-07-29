using System;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace AI_Crypto_Signal_Pro.Services
{
    /// <summary>
    /// Production-ready base API service for the current FastAPI backend.
    /// Base URL: http://localhost:8000/api/v1/
    /// </summary>
    public sealed class ApiService
    {
        private static readonly HttpClient _httpClient = new()
        {
            BaseAddress = new Uri("http://localhost:8000/api/v1/"),
            Timeout = TimeSpan.FromSeconds(30)
        };

        private static readonly JsonSerializerOptions _jsonOptions = new()
        {
            PropertyNameCaseInsensitive = true
        };

        public async Task<T?> GetAsync<T>(string endpoint, CancellationToken cancellationToken = default)
        {
            using var response = await _httpClient.GetAsync(endpoint, cancellationToken);
            return await ReadOrThrowAsync<T>(response, cancellationToken);
        }

        public async Task<TResponse?> PostAsync<TRequest, TResponse>(
            string endpoint, TRequest body, CancellationToken cancellationToken = default)
        {
            using var response = await _httpClient.PostAsJsonAsync(endpoint, body, _jsonOptions, cancellationToken);
            return await ReadOrThrowAsync<TResponse>(response, cancellationToken);
        }

        public async Task<TResponse?> DeleteAsync<TResponse>(
            string endpoint, CancellationToken cancellationToken = default)
        {
            using var response = await _httpClient.DeleteAsync(endpoint, cancellationToken);
            return await ReadOrThrowAsync<TResponse>(response, cancellationToken);
        }

        private static async Task<T?> ReadOrThrowAsync<T>(HttpResponseMessage response, CancellationToken cancellationToken)
        {
            if (!response.IsSuccessStatusCode)
            {
                var body = await response.Content.ReadAsStringAsync(cancellationToken);
                throw new HttpRequestException(
                    $"API request failed ({(int)response.StatusCode}): {body}");
            }

            return await response.Content.ReadFromJsonAsync<T>(_jsonOptions, cancellationToken);
        }

        public async Task<bool> HealthCheckAsync(CancellationToken cancellationToken = default)
        {
            try
            {
                // BaseAddress already ends in ".../api/v1/", so "health" resolves to
                // ".../api/v1/health" (the previous "../health" incorrectly resolved
                // to ".../api/health" and was never actually called anywhere).
                using var response = await _httpClient.GetAsync("health", cancellationToken);
                return response.IsSuccessStatusCode;
            }
            catch
            {
                return false;
            }
        }
    }
}
