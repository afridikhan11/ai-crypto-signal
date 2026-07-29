using System.Windows.Controls;
using AI_Crypto_Signal_Pro.ViewModels;

namespace AI_Crypto_Signal_Pro.Views
{
    public partial class AiPerformanceView : UserControl
    {
        public AiPerformanceView()
        {
            InitializeComponent();
            DataContext = new AiPerformanceViewModel();
        }
    }
}
