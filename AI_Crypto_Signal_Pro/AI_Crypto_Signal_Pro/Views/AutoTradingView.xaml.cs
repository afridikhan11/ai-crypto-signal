using System.Windows.Controls;
using AI_Crypto_Signal_Pro.ViewModels;

namespace AI_Crypto_Signal_Pro.Views
{
    public partial class AutoTradingView : UserControl
    {
        public AutoTradingView()
        {
            InitializeComponent();
            DataContext = new AutoTradingViewModel();

            Unloaded += (_, _) => (DataContext as AutoTradingViewModel)?.Cleanup();
        }
    }
}
