using System;
using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace AI_Crypto_Signal_Pro.Converters
{
    /// <summary>
    /// True -> Visible, False -> Collapsed. Pass parameter "Invert" to flip
    /// (False -> Visible, True -> Collapsed) - used by the Token Scanner tab
    /// to show/hide the results panel based on HasReport.
    /// </summary>
    public sealed class BooleanToVisibilityConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            var boolValue = value is bool b && b;
            var invert = string.Equals(parameter as string, "Invert", StringComparison.OrdinalIgnoreCase);
            if (invert)
                boolValue = !boolValue;

            return boolValue ? Visibility.Visible : Visibility.Collapsed;
        }

        public object ConvertBack(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            var isVisible = value is Visibility v && v == Visibility.Visible;
            var invert = string.Equals(parameter as string, "Invert", StringComparison.OrdinalIgnoreCase);
            return invert ? !isVisible : isVisible;
        }
    }
}
