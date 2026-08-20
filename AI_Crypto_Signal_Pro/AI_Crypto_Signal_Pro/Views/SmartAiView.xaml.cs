using System.Windows.Controls;
using System.Windows.Input;
using AI_Crypto_Signal_Pro.ViewModels;

namespace AI_Crypto_Signal_Pro.Views
{
    public partial class SmartAiView : UserControl
    {
        public SmartAiView()
        {
            InitializeComponent();
            DataContext = new SmartAiViewModel();

            Unloaded += (_, _) => (DataContext as SmartAiViewModel)?.Cleanup();
        }

        // Enter in the password box triggers the same LoginCommand the Sign In
        // button uses, passing the PasswordBox itself so the ViewModel reads the
        // password transiently without ever storing it.
        private void LoginPasswordBox_KeyDown(object sender, KeyEventArgs e)
        {
            if (e.Key != Key.Enter)
                return;

            if (DataContext is SmartAiViewModel vm && vm.LoginCommand.CanExecute(sender))
                vm.LoginCommand.Execute(sender);
        }
    }
}
