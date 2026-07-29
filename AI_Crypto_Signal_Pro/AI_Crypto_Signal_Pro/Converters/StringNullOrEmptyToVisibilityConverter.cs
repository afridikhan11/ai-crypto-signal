using System;
using System.Globalization;
using System.Windows;
using System.Windows.Data;

namespace AI_Crypto_Signal_Pro.Converters
{
    /// <summary>
    /// Non-null/non-empty string -> Visible, null/empty -> Collapsed. Used to
    /// show the Token Scanner error banner only while ErrorMessage is set.
    /// </summary>
    public sealed class StringNullOrEmptyToVisibilityConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            return string.IsNullOrWhiteSpace(value as string) ? Visibility.Collapsed : Visibility.Visible;
        }

        public object ConvertBack(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            throw new NotSupportedException();
        }
    }
}
