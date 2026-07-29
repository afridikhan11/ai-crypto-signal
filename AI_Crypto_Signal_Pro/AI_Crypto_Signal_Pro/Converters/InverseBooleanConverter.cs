using System;
using System.Globalization;
using System.Windows.Data;

namespace AI_Crypto_Signal_Pro.Converters
{
    /// <summary>Used to disable a control (e.g. the Execute button) once its bound bool flag becomes true.</summary>
    public sealed class InverseBooleanConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            return value is bool b ? !b : true;
        }

        public object ConvertBack(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            return value is bool b ? !b : false;
        }
    }
}
