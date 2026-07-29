using System.Windows.Controls;
using AI_Crypto_Signal_Pro.ViewModels;

namespace AI_Crypto_Signal_Pro.Views
{
    public partial class AiAssistantView : UserControl
    {
        public AiAssistantView()
        {
            InitializeComponent();
            DataContext = new AiAssistantViewModel();
        }
    }
}
