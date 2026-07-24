using System.Windows.Controls;
using AI_Crypto_Signal_Pro.ViewModels;

namespace AI_Crypto_Signal_Pro.Views
{
    public partial class StatisticsView : UserControl
    {
        public StatisticsView()
        {
            InitializeComponent();
            DataContext = new StatisticsViewModel();
        }
    }
}