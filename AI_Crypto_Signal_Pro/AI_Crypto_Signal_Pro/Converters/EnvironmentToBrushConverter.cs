using System;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;

namespace AI_Crypto_Signal_Pro.Converters
{
    /// <summary>
    /// Auto Trading's environment badge: amber for "testnet" (safe, no real
    /// funds), red for "mainnet" (real funds) - a deliberately loud visual
    /// difference so it's never ambiguous which one Execute is about to hit.
    /// </summary>
    public sealed class EnvironmentToBrushConverter : IValueConverter
    {
        private static readonly SolidColorBrush TestnetBrush = new(Color.FromRgb(0xFF, 0xB7, 0x4D));
        private static readonly SolidColorBrush MainnetBrush = new(Color.FromRgb(0xFF, 0x52, 0x52));
        private static readonly SolidColorBrush UnknownBrush = new(Color.FromRgb(0x9E, 0x9E, 0x9E));

        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            var env = value as string;
            return env switch
            {
                "testnet" => TestnetBrush,
                "mainnet" => MainnetBrush,
                _ => UnknownBrush,
            };
        }

        public object ConvertBack(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            throw new NotSupportedException();
        }
    }
}
