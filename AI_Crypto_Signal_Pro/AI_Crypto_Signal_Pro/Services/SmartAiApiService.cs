using System;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using AI_Crypto_Signal_Pro.Models;

namespace AI_Crypto_Signal_Pro.Services
{
    /// <summary>
    /// Thrown when a /smartai/* call comes back 401 and could not be recovered
    /// by a token refresh. The ViewModel catches this to drop back to the
    /// locked / login state. The in-memory tokens are always cleared before
    /// this is thrown, so catching it and showing the login screen is the only
    /// thing the caller has to do.
    /// </summary>
    public sealed class SmartAiUnauthorizedException : Exception
    {
        public SmartAiUnauthorizedException(string message) : base(message) { }
    }

    /// <summary>
    /// API surface for the Smart AI module (/api/v1/smartai/*). Mirrors
    /// <see cref="ApiService"/>'s approach - a single shared static HttpClient
    /// pointed at the same "http://localhost:8000/api/v1/" base URL and
    /// System.Text.Json with PropertyNameCaseInsensitive - but adds the two
    /// things the base ApiService has no concept of:
    ///
    ///   1. Bearer auth. The access token lives IN MEMORY here (never in a
    ///      ViewModel property, never on disk in plaintext). Every guarded call
    ///      attaches "Authorization: Bearer &lt;token&gt;" per-request via
    ///      HttpRequestMessage, so nothing is written onto the shared client's
    ///      DefaultRequestHeaders (which would leak into the base ApiService's
    ///      calls that share no client here, but the per-request form is the
    ///      correct pattern regardless).
    ///
    ///   2. Centralised 401 handling. A guarded call that returns 401 triggers
    ///      exactly one silent refresh attempt (if a refresh token is held);
    ///      on success the original call is retried once, on failure the whole
    ///      session is cleared and <see cref="SmartAiUnauthorizedException"/>
    ///      is thrown so the UI can lock.
    ///
    /// The session is MEMORY-ONLY: both tokens live in this instance and die
    /// with the process - nothing is written to disk (no plaintext, and no
    /// DPAPI dependency), so a restart returns to the login screen.
    /// </summary>
    public sealed class SmartAiApiService
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

        // In-memory only. No ViewModel and no disk file ever holds the access token.
        private string? _accessToken;
        private string? _refreshToken;

        /// <summary>True once a login (or a restored + refreshed session) has produced an access token.</summary>
        public bool IsAuthenticated => !string.IsNullOrEmpty(_accessToken);

        // ============================ Auth ============================

        /// <summary>
        /// POST /smartai/auth/login. The password is used for this one request
        /// and immediately discarded - it is never assigned to a field.
        /// On success the tokens are held in memory only. <paramref name="rememberMe"/>
        /// is accepted for UI symmetry but does not persist anything - the
        /// session is memory-only (no disk, no DPAPI dependency).
        /// </summary>
        public async Task LoginAsync(string password, bool rememberMe, CancellationToken cancellationToken = default)
        {
            _ = rememberMe;  // memory-only session; nothing is persisted
            using var response = await _httpClient.PostAsJsonAsync(
                "smartai/auth/login",
                new SmartAiLoginRequestDto { Password = password },
                _jsonOptions,
                cancellationToken);

            if (!response.IsSuccessStatusCode)
            {
                var body = await response.Content.ReadAsStringAsync(cancellationToken);
                throw new HttpRequestException($"Login failed ({(int)response.StatusCode}): {body}");
            }

            var tokens = await response.Content.ReadFromJsonAsync<SmartAiLoginResponseDto>(_jsonOptions, cancellationToken)
                         ?? throw new HttpRequestException("Login returned an empty response.");

            _accessToken = tokens.AccessToken;
            _refreshToken = tokens.RefreshToken;
        }

        /// <summary>
        /// Memory-only session: there is nothing persisted to restore across
        /// restarts, so this always reports "no session" and the caller shows
        /// the login screen. Kept so the startup flow has a single call site to
        /// change if disk persistence is ever added.
        /// </summary>
        public Task<bool> TryRestoreSessionAsync(CancellationToken cancellationToken = default)
            => Task.FromResult(false);

        /// <summary>
        /// POST /smartai/auth/refresh using the in-memory refresh token.
        /// Returns true and updates the access token on success; false on any
        /// failure (expired/invalid refresh token, network error, no token).
        /// </summary>
        private async Task<bool> TryRefreshAsync(CancellationToken cancellationToken)
        {
            if (string.IsNullOrEmpty(_refreshToken))
                return false;

            try
            {
                using var response = await _httpClient.PostAsJsonAsync(
                    "smartai/auth/refresh",
                    new SmartAiRefreshRequestDto { RefreshToken = _refreshToken },
                    _jsonOptions,
                    cancellationToken);

                if (!response.IsSuccessStatusCode)
                    return false;

                var refreshed = await response.Content.ReadFromJsonAsync<SmartAiRefreshResponseDto>(_jsonOptions, cancellationToken);
                if (refreshed == null || string.IsNullOrEmpty(refreshed.AccessToken))
                    return false;

                _accessToken = refreshed.AccessToken;
                return true;
            }
            catch (OperationCanceledException)
            {
                throw;
            }
            catch
            {
                return false;
            }
        }

        /// <summary>Drops the in-memory tokens. Called on an unrecoverable 401
        /// and on explicit logout.</summary>
        public void ClearSession()
        {
            _accessToken = null;
            _refreshToken = null;
        }

        // ===================== Guarded endpoints =====================

        public Task<SmartAiStatusDto?> GetStatusAsync(CancellationToken ct = default)
            => GetGuardedAsync<SmartAiStatusDto>("smartai/status", ct);

        public Task<SmartAiRunnerStatusDto?> GetRunnerStatusAsync(CancellationToken ct = default)
            => GetGuardedAsync<SmartAiRunnerStatusDto>("smartai/runner/status", ct);

        public Task<SmartAiSignalDto[]?> GetSignalsAsync(string strategyId, int limit = 50, CancellationToken ct = default)
            => GetGuardedAsync<SmartAiSignalDto[]>(
                $"smartai/signals?strategy_id={Uri.EscapeDataString(strategyId)}&limit={limit}", ct);

        public Task<SmartAiPerformanceDto?> GetPerformanceAsync(string strategyId, CancellationToken ct = default)
            => GetGuardedAsync<SmartAiPerformanceDto>(
                $"smartai/performance?strategy_id={Uri.EscapeDataString(strategyId)}", ct);

        public Task<SmartAiStrategyToggleResponseDto?> EnableStrategyAsync(string strategyId, CancellationToken ct = default)
            => PostGuardedAsync<SmartAiStrategyToggleResponseDto>(
                $"smartai/strategies/{Uri.EscapeDataString(strategyId)}/enable", ct);

        public Task<SmartAiStrategyToggleResponseDto?> DisableStrategyAsync(string strategyId, CancellationToken ct = default)
            => PostGuardedAsync<SmartAiStrategyToggleResponseDto>(
                $"smartai/strategies/{Uri.EscapeDataString(strategyId)}/disable", ct);

        // ===================== HTTP plumbing =====================

        private async Task<T?> GetGuardedAsync<T>(string endpoint, CancellationToken ct)
        {
            using var response = await SendGuardedAsync(HttpMethod.Get, endpoint, ct);
            return await ReadOrThrowAsync<T>(response, ct);
        }

        // Every guarded POST in this module (enable/disable) has an empty body,
        // which is what makes the "retry once after refresh" path safe - there
        // is no request content to rebuild.
        private async Task<T?> PostGuardedAsync<T>(string endpoint, CancellationToken ct)
        {
            using var response = await SendGuardedAsync(HttpMethod.Post, endpoint, ct);
            return await ReadOrThrowAsync<T>(response, ct);
        }

        /// <summary>
        /// Sends a bearer-authenticated request and applies the centralised 401
        /// policy: on a 401, try one silent refresh, and if that succeeds retry
        /// the request exactly once. If there is no usable session left, clears
        /// everything and throws <see cref="SmartAiUnauthorizedException"/>.
        /// </summary>
        private async Task<HttpResponseMessage> SendGuardedAsync(HttpMethod method, string endpoint, CancellationToken ct)
        {
            var response = await SendOnceAsync(method, endpoint, ct);

            if (response.StatusCode == HttpStatusCode.Unauthorized)
            {
                response.Dispose();

                var refreshed = await TryRefreshAsync(ct);
                if (refreshed)
                {
                    response = await SendOnceAsync(method, endpoint, ct);
                    if (response.StatusCode != HttpStatusCode.Unauthorized)
                        return response;

                    response.Dispose();
                }

                ClearSession();
                throw new SmartAiUnauthorizedException("Smart AI session expired. Please sign in again.");
            }

            return response;
        }

        private Task<HttpResponseMessage> SendOnceAsync(HttpMethod method, string endpoint, CancellationToken ct)
        {
            var request = new HttpRequestMessage(method, endpoint);
            if (!string.IsNullOrEmpty(_accessToken))
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _accessToken);

            return _httpClient.SendAsync(request, ct);
        }

        private static async Task<T?> ReadOrThrowAsync<T>(HttpResponseMessage response, CancellationToken ct)
        {
            if (!response.IsSuccessStatusCode)
            {
                var body = await response.Content.ReadAsStringAsync(ct);
                throw new HttpRequestException($"Smart AI request failed ({(int)response.StatusCode}): {body}");
            }

            return await response.Content.ReadFromJsonAsync<T>(_jsonOptions, ct);
        }
    }
}
