using System.Windows.Controls;
using AI_Crypto_Signal_Pro.ViewModels;

namespace AI_Crypto_Signal_Pro.Views
{
    public partial class AccountView : UserControl
    {
        public AccountView()
        {
            InitializeComponent();
            DataContext = new AccountViewModel();

            // Stop background auto-refresh polling once this screen is navigated
            // away from (removed from the visual tree) so it doesn't keep hitting
            // the backend/Binance after the user can no longer see the data.
            Unloaded += (_, _) => (DataContext as AccountViewModel)?.StopAutoRefresh();
        }
    }
}
