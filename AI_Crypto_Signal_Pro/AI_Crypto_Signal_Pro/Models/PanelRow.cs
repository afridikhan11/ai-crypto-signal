namespace AI_Crypto_Signal_Pro.Models
{
    /// <summary>
    /// A single label/value row for the Dashboard's dense Account/AI/Market panels.
    /// ValueColor is a hex string (e.g. "#00E676") - WPF's binding engine converts it
    /// to a Brush automatically via BrushConverter, same pattern already used by
    /// MainWindowViewModel's status bar colors.
    /// </summary>
    public sealed class PanelRow
    {
        public string Label { get; set; } = string.Empty;
        public string Value { get; set; } = string.Empty;
        public string ValueColor { get; set; } = "#E6E8EC";
    }
}
