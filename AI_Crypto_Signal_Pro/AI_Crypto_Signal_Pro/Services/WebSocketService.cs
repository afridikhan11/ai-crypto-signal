using System;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace AI_Crypto_Signal_Pro.Services
{
    /// <summary>
    /// Persistent connection to the backend's GET /ws/signals endpoint.
    /// Raises SignalReceived whenever a real (non-heartbeat) message arrives -
    /// consumers re-fetch via ApiService rather than parsing the raw push
    /// payload directly, so there is one single source of truth for the
    /// signal shape (the REST /signals endpoint's SignalResponse schema).
    ///
    /// Auto-reconnects with exponential backoff (capped at 30s) if the
    /// connection drops or the backend isn't up yet. Singleton so the status
    /// bar and any view can observe the same live connection state.
    /// </summary>
    public sealed class WebSocketService
    {
        public static WebSocketService Instance { get; } = new();

        private const string Url = "ws://localhost:8000/ws/signals";

        private CancellationTokenSource? _cts;
        private bool _isConnected;

        public bool IsConnected
        {
            get => _isConnected;
            private set
            {
                if (_isConnected == value)
                    return;
                _isConnected = value;
                ConnectionStateChanged?.Invoke(this, value);
            }
        }

        /// <summary>Fires whenever the connection is established or lost.</summary>
        public event EventHandler<bool>? ConnectionStateChanged;

        /// <summary>Fires whenever a real (non-heartbeat) push message arrives from the backend.</summary>
        public event EventHandler? SignalReceived;

        private WebSocketService()
        {
        }

        /// <summary>Idempotent - calling this more than once has no additional effect while already running.</summary>
        public void Start()
        {
            if (_cts != null)
                return;

            _cts = new CancellationTokenSource();
            _ = RunAsync(_cts.Token);
        }

        public void Stop()
        {
            _cts?.Cancel();
            _cts = null;
            IsConnected = false;
        }

        private async Task RunAsync(CancellationToken token)
        {
            var backoff = TimeSpan.FromSeconds(1);
            var buffer = new byte[16 * 1024];

            while (!token.IsCancellationRequested)
            {
                try
                {
                    using var socket = new ClientWebSocket();
                    await socket.ConnectAsync(new Uri(Url), token);
                    IsConnected = true;
                    backoff = TimeSpan.FromSeconds(1);

                    while (socket.State == WebSocketState.Open && !token.IsCancellationRequested)
                    {
                        var result = await socket.ReceiveAsync(new ArraySegment<byte>(buffer), token);

                        if (result.MessageType == WebSocketMessageType.Close)
                            break;

                        var text = Encoding.UTF8.GetString(buffer, 0, result.Count);

                        // The server sends {"type":"ping"} every 30s purely to keep the
                        // connection alive - only real signal pushes should trigger a refresh.
                        if (text.Contains("\"type\":\"ping\"", StringComparison.OrdinalIgnoreCase))
                            continue;

                        SignalReceived?.Invoke(this, EventArgs.Empty);
                    }
                }
                catch (OperationCanceledException)
                {
                    break;
                }
                catch
                {
                    // Connection failed, was refused (backend not up yet), or dropped -
                    // fall through to the reconnect/backoff logic below.
                }
                finally
                {
                    IsConnected = false;
                }

                if (token.IsCancellationRequested)
                    break;

                try
                {
                    await Task.Delay(backoff, token);
                }
                catch (OperationCanceledException)
                {
                    break;
                }

                backoff = TimeSpan.FromSeconds(Math.Min(backoff.TotalSeconds * 2, 30));
            }
        }
    }
}
