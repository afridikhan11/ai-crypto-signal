using System.Windows.Controls;
using AI_Crypto_Signal_Pro.ViewModels;

namespace AI_Crypto_Signal_Pro.Views
{
    public partial class PortfolioIntelligenceView : UserControl
    {
        public PortfolioIntelligenceView()
        {
            InitializeComponent();
            DataContext = new PortfolioIntelligenceViewModel();
        }
    }
}
