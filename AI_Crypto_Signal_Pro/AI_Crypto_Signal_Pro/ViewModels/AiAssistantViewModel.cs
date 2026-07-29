using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using AI_Crypto_Signal_Pro.Models;
using AI_Crypto_Signal_Pro.Services;

namespace AI_Crypto_Signal_Pro.ViewModels
{
    /// <summary>
    /// AI Assistant screen - a single conversational front end over the backend's
    /// /agent/query endpoint (Phases 2-6). This is intentionally the ONE screen for
    /// four backend features rather than four separate screens: the AI Trading Agent,
    /// Evidence &amp; Reasoning Engine, Research &amp; Market Intelligence Engine, and AI
    /// Trading Coach are all sub-sections of a single AgentQueryResponse already
    /// composed server-side (see app/schemas/agent.py) - splitting them into separate
    /// WPF screens would mean either four screens that each show one slice of the same
    /// answer, or duplicating the request/response plumbing four times. Neither serves
    /// "one unified trading platform, no duplicate screens."
    ///
    /// This ViewModel never computes a score, a decision, or any market analysis
    /// itself - every field rendered here is exactly what the backend returned. If a
    /// section is null (e.g. no coach_advice because the question wasn't one of the 7
    /// practical questions it covers), the view shows nothing for that section rather
    /// than fabricating a placeholder.
    /// </summary>
    public partial class AiAssistantViewModel : ObservableObject
    {
        private readonly ApiService _apiService = new();

        // One session per screen lifetime, so follow-up questions ("why did it fail?",
        // "compare it with ETH") work without the user repeating the asset name - see
        // app/agent/conversation_context.py. Session state is server-side, in-memory
        // only, and expires after an hour of idleness; ClearSessionCommand lets the
        // user reset it on demand instead of waiting.
        private readonly string _sessionId = $"wpf-{Guid.NewGuid():N}";

        [ObservableProperty]
        private string inputText = string.Empty;

        [ObservableProperty]
        private bool isLoading;

        [ObservableProperty]
        private string? errorMessage;

        public ObservableCollection<AgentConversationTurn> Conversation { get; } = new();

        [RelayCommand]
        private async Task Send()
        {
            var message = InputText?.Trim();
            if (string.IsNullOrEmpty(message) || IsLoading)
                return;

            InputText = string.Empty;
            ErrorMessage = null;

            Conversation.Add(new AgentConversationTurn { IsUser = true, Text = message });

            IsLoading = true;
            try
            {
                var request = new AgentQueryRequestDto { Message = message, SessionId = _sessionId };
                var response = await _apiService.PostAsync<AgentQueryRequestDto, AgentQueryResponseDto>("agent/query", request);

                if (response is null)
                {
                    ErrorMessage = "The Trading Agent returned an empty response.";
                    return;
                }

                Conversation.Add(new AgentConversationTurn
                {
                    IsUser = false,
                    Text = response.Narrative,
                    Response = response,
                });
            }
            catch (Exception ex)
            {
                ErrorMessage = $"Trading Agent request failed: {ex.Message}";
            }
            finally
            {
                IsLoading = false;
            }
        }

        [RelayCommand]
        private async Task ClearSession()
        {
            if (IsLoading)
                return;

            IsLoading = true;
            ErrorMessage = null;
            try
            {
                await _apiService.DeleteAsync<ClearSessionResponseDto>($"agent/session?session_id={Uri.EscapeDataString(_sessionId)}");
                Conversation.Clear();
            }
            catch (Exception ex)
            {
                ErrorMessage = $"Could not clear session: {ex.Message}";
            }
            finally
            {
                IsLoading = false;
            }
        }
    }
}
