using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using AI_Crypto_Signal_Pro.Services;
using AI_Crypto_Signal_Pro.Views;
using AI_Crypto_Signal_Pro.ViewModels;

namespace AI_Crypto_Signal_Pro
{
    public partial class MainWindow : Window
    {
        private readonly NavigationService _navigation;

        public MainWindow()
        {
            InitializeComponent();

            DataContext = new MainWindowViewModel();
            WebSocketService.Instance.Start();
            Closed += (_, _) => WebSocketService.Instance.Stop();

            _navigation = new NavigationService(MainContent);

            ShowDashboard();

            NavigationListBox.SelectionChanged += NavigationListBox_SelectionChanged;
        }

        private void ShowDashboard()
        {
            _navigation.Navigate(new DashboardView
            {
                DataContext = new DashboardViewModel()
            });
        }

        private void NavigationListBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            switch (NavigationListBox.SelectedIndex)
            {
                case 0:
                    ShowDashboard();
                    break;

                case 1:
                    _navigation.Navigate(new LiveSignalsView());
                    break;

                case 2:
                    _navigation.Navigate(new GoldSignalsView());
                    break;

                case 3:
                    _navigation.Navigate(new StatisticsView());
                    break;

                case 4:
                    _navigation.Navigate(new HistoryView());
                    break;

                case 5:
                    _navigation.Navigate(new AccountView());
                    break;

                case 6:
                    _navigation.Navigate(new AutoTradingView());
                    break;

                case 7:
                    _navigation.Navigate(new TokenScannerView());
                    break;

                case 8:
                    _navigation.Navigate(new AiAssistantView());
                    break;

                case 9:
                    _navigation.Navigate(new PortfolioIntelligenceView());
                    break;

                case 10:
                    _navigation.Navigate(new AiPerformanceView());
                    break;

                case 11:
                    _navigation.Navigate(new SmartAiView());
                    break;

                case 12:
                    _navigation.Navigate(new SettingsView());
                    break;

                default:
                    ShowDashboard();
                    break;
            }
        }

        private void TitleBar_MouseDown(object sender, MouseButtonEventArgs e)
        {
            if (e.LeftButton == MouseButtonState.Pressed)
                DragMove();
        }

        private void MinimizeButton_Click(object sender, RoutedEventArgs e)
        {
            WindowState = WindowState.Minimized;
        }

        private void MaximizeButton_Click(object sender, RoutedEventArgs e)
        {
            WindowState = WindowState == WindowState.Maximized
                ? WindowState.Normal
                : WindowState.Maximized;
        }

        private void CloseButton_Click(object sender, RoutedEventArgs e)
        {
            Close();
        }
    }
}