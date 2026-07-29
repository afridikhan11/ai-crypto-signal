using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Windows.Data;

namespace AI_Crypto_Signal_Pro.Converters
{
    /// <summary>
    /// Joins a List&lt;string&gt; (or any IEnumerable) into a single comma-separated
    /// string for display - e.g. ContractSecurityAnalysis.SourcesUsed
    /// ("GoPlus Security", "Honeypot.is") -> "GoPlus Security, Honeypot.is".
    /// Empty/null collections become "Not Available" so the caption never
    /// renders blank.
    /// </summary>
    public sealed class StringJoinConverter : IValueConverter
    {
        public object Convert(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            if (value is IEnumerable enumerable and not string)
            {
                var items = enumerable.Cast<object>().Select(o => o?.ToString() ?? string.Empty)
                    .Where(s => !string.IsNullOrWhiteSpace(s))
                    .ToList();
                return items.Count > 0 ? string.Join(", ", items) : "Not Available";
            }
            return value?.ToString() ?? "Not Available";
        }

        public object ConvertBack(object? value, Type targetType, object parameter, CultureInfo culture)
        {
            throw new NotSupportedException();
        }
    }
}
