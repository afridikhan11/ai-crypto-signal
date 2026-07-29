using System.Windows.Controls;
using AI_Crypto_Signal_Pro.ViewModels;

namespace AI_Crypto_Signal_Pro.Views
{
    public partial class GoldSignalsView : UserControl
    {
        public GoldSignalsView()
        {
            InitializeComponent();
            DataContext = new GoldSignalsViewModel();

            Unloaded += (_, _) => (DataContext as GoldSignalsViewModel)?.Cleanup();
        }
    }
}
