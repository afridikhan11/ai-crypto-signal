using System.Windows.Controls;
using AI_Crypto_Signal_Pro.ViewModels;

namespace AI_Crypto_Signal_Pro.Views
{
    public partial class TokenScannerView : UserControl
    {
        public TokenScannerView()
        {
            InitializeComponent();
            DataContext = new TokenScannerViewModel();
        }
    }
}
