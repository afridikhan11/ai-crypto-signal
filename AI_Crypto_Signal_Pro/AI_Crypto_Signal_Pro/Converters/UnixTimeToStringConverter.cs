using System;
using System.Globalization;
using System.Windows.Data;

namespace AI_Crypto_Signal_Pro.Converters
{
    /// <summary>Converts a Binance epoch-millisecond timestamp (long) into a readable local date/time string.</summary>
    public sealed class UnixTimeToStringConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            if (value is long ms && ms > 0)
            {
                return DateTimeOffset.FromUnixTimeMilliseconds(ms).LocalDateTime.ToString("yyyy-MM-dd HH:mm:ss");
            }
            return string.Empty;
        }

        public object ConvertBack(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            throw new NotSupportedException();
        }
    }
}
