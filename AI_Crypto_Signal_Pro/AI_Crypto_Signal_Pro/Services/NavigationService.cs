using System;
using System.Windows.Controls;

namespace AI_Crypto_Signal_Pro.Services
{
    public class NavigationService
    {
        private readonly ContentControl _host;

        public NavigationService(ContentControl host)
        {
            _host = host;
        }

        public void Navigate(UserControl view)
        {
            _host.Content = view;
        }

        public void Clear()
        {
            _host.Content = null;
        }
    }
}
