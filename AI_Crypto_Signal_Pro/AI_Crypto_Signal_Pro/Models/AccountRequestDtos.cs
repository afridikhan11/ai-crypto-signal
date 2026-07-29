using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>Body for POST /api/v1/account/credentials.</summary>
    public sealed class SaveCredentialsRequestDto
    {
        [JsonPropertyName("api_key")]
        public string ApiKey { get; set; } = string.Empty;

        [JsonPropertyName("api_secret")]
        public string ApiSecret { get; set; } = string.Empty;

        [JsonPropertyName("testnet")]
        public bool Testnet { get; set; }
    }

    /// <summary>Body for POST /api/v1/account/test-connection. All fields optional.</summary>
    public sealed class TestConnectionRequestDto
    {
        [JsonPropertyName("api_key")]
        public string? ApiKey { get; set; }

        [JsonPropertyName("api_secret")]
        public string? ApiSecret { get; set; }

        [JsonPropertyName("testnet")]
        public bool? Testnet { get; set; }
    }

    /// <summary>Matches FastAPI CredentialsStatusResponse schema.</summary>
    public sealed class CredentialsStatusDto
    {
        [JsonPropertyName("has_credentials")]
        public bool HasCredentials { get; set; }

        [JsonPropertyName("environment")]
        public string? Environment { get; set; }
    }

    /// <summary>Matches FastAPI ConnectionTestResult schema.</summary>
    public sealed class ConnectionTestResultDto
    {
        [JsonPropertyName("success")]
        public bool Success { get; set; }

        [JsonPropertyName("environment")]
        public string Environment { get; set; } = string.Empty;

        [JsonPropertyName("message")]
        public string Message { get; set; } = string.Empty;

        [JsonPropertyName("account_type")]
        public string? AccountType { get; set; }

        [JsonPropertyName("permissions")]
        public List<string> Permissions { get; set; } = new();
    }
}
