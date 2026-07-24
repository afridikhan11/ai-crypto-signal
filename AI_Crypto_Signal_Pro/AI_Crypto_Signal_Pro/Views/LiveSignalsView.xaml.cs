using System.Windows.Controls;
using AI_Crypto_Signal_Pro.ViewModels;

namespace AI_Crypto_Signal_Pro.Views
{
    public partial class LiveSignalsView : UserControl
    {
        public LiveSignalsView()
        {
            InitializeComponent();
            DataContext = new LiveSignalsViewModel();
        }
    }
}
