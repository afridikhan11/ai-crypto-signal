using System;
using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace AI_Crypto_Signal_Pro.Converters
{
    /// <summary>
    /// Non-null -> Visible, null -> Collapsed. Used by the AI Assistant, Portfolio
    /// Intelligence, and AI Performance screens to hide a section entirely when the
    /// backend genuinely has nothing to report there (e.g. no coach_advice because the
    /// question wasn't one of the 7 practical questions it covers) rather than showing
    /// an empty card - honest "not shown" instead of a fabricated placeholder.
    /// Pass parameter "Invert" to flip (null -> Visible, non-null -> Collapsed).
    /// </summary>
    public sealed class NullToVisibilityConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            var isNull = value is null;
            var invert = string.Equals(parameter as string, "Invert", StringComparison.OrdinalIgnoreCase);
            if (invert)
                isNull = !isNull;

            return isNull ? Visibility.Collapsed : Visibility.Visible;
        }

        public object ConvertBack(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            throw new NotSupportedException();
        }
    }
}
