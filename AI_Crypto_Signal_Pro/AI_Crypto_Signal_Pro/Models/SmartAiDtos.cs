using System;
using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace AI_Crypto_Signal_Pro.Models
{
    // =====================================================================
    // Smart AI module DTOs. Bind to the FastAPI /api/v1/smartai/* contract.
    // Same style as the rest of Models/*: sealed classes, System.Text.Json
    // [JsonPropertyName] mapping (ApiService uses PropertyNameCaseInsensitive,
    // but the explicit names keep the snake_case wire format unambiguous and
    // match the existing DTOs one-for-one).
    // =====================================================================

    /// <summary>Body for POST /smartai/auth/login. The password is passed
    /// transiently for this single call only and is never stored on the
    /// client (no property on any ViewModel holds it).</summary>
    public sealed class SmartAiLoginRequestDto
    {
        [JsonPropertyName("password")]
        public string Password { get; set; } = string.Empty;
    }

    /// <summary>Body for POST /smartai/auth/refresh.</summary>
    public sealed class SmartAiRefreshRequestDto
    {
        [JsonPropertyName("refresh_token")]
        public string RefreshToken { get; set; } = string.Empty;
    }

    /// <summary>Response of POST /smartai/auth/login.</summary>
    public sealed class SmartAiLoginResponseDto
    {
        [JsonPropertyName("access_token")]
        public string AccessToken { get; set; } = string.Empty;

        [JsonPropertyName("refresh_token")]
        public string RefreshToken { get; set; } = string.Empty;

        [JsonPropertyName("token_type")]
        public string TokenType { get; set; } = string.Empty;

        [JsonPropertyName("expires_in_minutes")]
        public int ExpiresInMinutes { get; set; }
    }

    /// <summary>Response of POST /smartai/auth/refresh.</summary>
    public sealed class SmartAiRefreshResponseDto
    {
        [JsonPropertyName("access_token")]
        public string AccessToken { get; set; } = string.Empty;

        [JsonPropertyName("expires_in_minutes")]
        public int ExpiresInMinutes { get; set; }
    }

    /// <summary>One strategy as returned by /smartai/status and /smartai/strategies.</summary>
    public sealed class SmartAiStrategyDto
    {
        [JsonPropertyName("strategy_id")]
        public string StrategyId { get; set; } = string.Empty;

        [JsonPropertyName("display_name")]
        public string DisplayName { get; set; } = string.Empty;

        [JsonPropertyName("enabled")]
        public bool Enabled { get; set; }
    }

    /// <summary>Response of GET /smartai/status.</summary>
    public sealed class SmartAiStatusDto
    {
        [JsonPropertyName("module_enabled")]
        public bool ModuleEnabled { get; set; }

        [JsonPropertyName("testnet")]
        public bool Testnet { get; set; }

        [JsonPropertyName("strategies")]
        public List<SmartAiStrategyDto> Strategies { get; set; } = new();
    }

    /// <summary>One strategy row as returned by /smartai/runner/status - adds the
    /// live runner counters on top of the plain strategy fields.</summary>
    public sealed class SmartAiRunnerStrategyDto
    {
        [JsonPropertyName("strategy_id")]
        public string StrategyId { get; set; } = string.Empty;

        [JsonPropertyName("display_name")]
        public string DisplayName { get; set; } = string.Empty;

        [JsonPropertyName("enabled")]
        public bool Enabled { get; set; }

        [JsonPropertyName("running")]
        public bool Running { get; set; }

        [JsonPropertyName("evaluations")]
        public long Evaluations { get; set; }

        [JsonPropertyName("signals")]
        public long Signals { get; set; }

        [JsonPropertyName("restarts")]
        public int Restarts { get; set; }

        [JsonPropertyName("last_error")]
        public string? LastError { get; set; }
    }

    /// <summary>Response of GET /smartai/runner/status.</summary>
    public sealed class SmartAiRunnerStatusDto
    {
        [JsonPropertyName("running")]
        public bool Running { get; set; }

        [JsonPropertyName("strategies")]
        public List<SmartAiRunnerStrategyDto> Strategies { get; set; } = new();
    }

    /// <summary>Response of POST /smartai/strategies/{id}/enable and /disable.</summary>
    public sealed class SmartAiStrategyToggleResponseDto
    {
        [JsonPropertyName("strategy_id")]
        public string StrategyId { get; set; } = string.Empty;

        [JsonPropertyName("enabled")]
        public bool Enabled { get; set; }
    }

    /// <summary>One entry in a signal's rules_fired list - the "which rules fired"
    /// breakdown that the Signals table expands to show.</summary>
    public sealed class SmartAiRuleFiredDto
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = string.Empty;

        [JsonPropertyName("passed")]
        public bool Passed { get; set; }

        [JsonPropertyName("detail")]
        public string? Detail { get; set; }
    }

    /// <summary>One signal as returned by GET /smartai/signals.</summary>
    public sealed class SmartAiSignalDto
    {
        [JsonPropertyName("id")]
        public string Id { get; set; } = string.Empty;

        [JsonPropertyName("strategy_id")]
        public string StrategyId { get; set; } = string.Empty;

        [JsonPropertyName("symbol")]
        public string Symbol { get; set; } = string.Empty;

        [JsonPropertyName("direction")]
        public string Direction { get; set; } = string.Empty;

        [JsonPropertyName("status")]
        public string Status { get; set; } = string.Empty;

        [JsonPropertyName("entry_price")]
        public decimal EntryPrice { get; set; }

        [JsonPropertyName("stop_loss")]
        public decimal StopLoss { get; set; }

        [JsonPropertyName("take_profit")]
        public decimal TakeProfit { get; set; }

        [JsonPropertyName("risk_reward")]
        public decimal RiskReward { get; set; }

        // Integer 0-100, same as the backend Signal.confidence (StrategySignal
        // builds it as an int); shown as a plain number, not a percentage.
        [JsonPropertyName("confidence")]
        public int Confidence { get; set; }

        [JsonPropertyName("rules_fired")]
        public List<SmartAiRuleFiredDto>? RulesFired { get; set; }

        [JsonPropertyName("created_at")]
        public DateTime CreatedAt { get; set; }
    }

    /// <summary>Response of GET /smartai/performance.</summary>
    public sealed class SmartAiPerformanceDto
    {
        [JsonPropertyName("strategy_id")]
        public string StrategyId { get; set; } = string.Empty;

        [JsonPropertyName("total_trades")]
        public int TotalTrades { get; set; }

        [JsonPropertyName("wins")]
        public int Wins { get; set; }

        [JsonPropertyName("losses")]
        public int Losses { get; set; }

        [JsonPropertyName("win_rate")]
        public double WinRate { get; set; }

        [JsonPropertyName("note")]
        public string? Note { get; set; }
    }
}
