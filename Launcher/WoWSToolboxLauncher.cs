using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;
using System.Windows.Forms;

[assembly: AssemblyTitle("WoWS Toolbox")]
[assembly: AssemblyDescription("WoWS Toolbox application launcher")]
[assembly: AssemblyCompany("WoWS Toolbox contributors")]
[assembly: AssemblyProduct("WoWS Toolbox")]
[assembly: AssemblyCopyright("Copyright (c) 2026 WoWS Toolbox contributors")]
[assembly: AssemblyVersion("5.0.68.0")]
[assembly: AssemblyFileVersion("5.0.68.0")]
[assembly: AssemblyInformationalVersion("5.0.68")]

namespace WoWSToolboxLauncher
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            string packageRoot = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar
            );
            bool english = ReadLanguage(packageRoot) == "en";
            string scriptPath = Path.Combine(
                packageRoot, "GUI", "Launch-Gui.ps1"
            );
            string powerShell = FindPowerShell();

            bool checkOnly = Array.Exists(
                args ?? new string[0],
                value => string.Equals(
                    value, "--check", StringComparison.OrdinalIgnoreCase
                )
            );
            if (checkOnly)
            {
                if (!File.Exists(scriptPath)) return 20;
                if (string.IsNullOrEmpty(powerShell)) return 21;
                return 0;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            if (!File.Exists(scriptPath))
            {
                ShowError(
                    english,
                    english
                        ? "The WoWS Toolbox startup script is missing:\n\n" + scriptPath
                        : "WoWS Toolbox 시작 스크립트를 찾지 못했습니다:\n\n" + scriptPath
                );
                return 20;
            }
            if (string.IsNullOrEmpty(powerShell))
            {
                ShowError(
                    english,
                    english
                        ? "Windows PowerShell 5.1 or PowerShell 7 was not found."
                        : "Windows PowerShell 5.1 또는 PowerShell 7을 찾지 못했습니다."
                );
                return 21;
            }

            try
            {
                ProcessStartInfo startInfo = new ProcessStartInfo
                {
                    FileName = powerShell,
                    Arguments =
                        "-STA -NoLogo -NoProfile -WindowStyle Hidden " +
                        "-ExecutionPolicy Bypass -File " + Quote(scriptPath),
                    WorkingDirectory = packageRoot,
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    WindowStyle = ProcessWindowStyle.Hidden,
                    ErrorDialog = false
                };
                Process process = Process.Start(startInfo);
                if (process == null)
                {
                    throw new InvalidOperationException(
                        "The PowerShell process was not created."
                    );
                }
                return 0;
            }
            catch (Exception error)
            {
                WriteLauncherError(packageRoot, error);
                ShowError(
                    english,
                    english
                        ? "WoWS Toolbox could not be started.\n\n" + error.Message
                        : "WoWS Toolbox를 시작하지 못했습니다.\n\n" + error.Message
                );
                return 22;
            }
        }

        private static string ReadLanguage(string packageRoot)
        {
            try
            {
                string marker = Path.Combine(packageRoot, "app-language.txt");
                if (!File.Exists(marker)) return "en";
                string value = File.ReadAllText(marker, Encoding.UTF8)
                    .Trim().ToLowerInvariant();
                return value == "ko" ? "ko" : "en";
            }
            catch
            {
                return "en";
            }
        }

        private static string FindPowerShell()
        {
            List<string> candidates = new List<string>();
            AddPowerShell7Candidates(candidates);

            string pathValue = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string directory in pathValue.Split(Path.PathSeparator))
            {
                string trimmed = directory.Trim().Trim('"');
                if (trimmed.Length == 0) continue;
                try
                {
                    candidates.Add(Path.Combine(trimmed, "pwsh.exe"));
                }
                catch
                {
                    // Ignore malformed PATH entries.
                }
            }

            string systemRoot =
                Environment.GetEnvironmentVariable("SystemRoot") ?? @"C:\Windows";
            candidates.Add(Path.Combine(
                systemRoot,
                "System32",
                "WindowsPowerShell",
                "v1.0",
                "powershell.exe"
            ));

            HashSet<string> seen = new HashSet<string>(
                StringComparer.OrdinalIgnoreCase
            );
            foreach (string candidate in candidates)
            {
                if (string.IsNullOrWhiteSpace(candidate)) continue;
                string fullPath;
                try
                {
                    fullPath = Path.GetFullPath(candidate);
                }
                catch
                {
                    continue;
                }
                if (!seen.Add(fullPath)) continue;
                if (
                    fullPath.IndexOf(
                        @"\Microsoft\WindowsApps\",
                        StringComparison.OrdinalIgnoreCase
                    ) >= 0
                )
                {
                    continue;
                }
                if (File.Exists(fullPath)) return fullPath;
            }
            return null;
        }

        private static void AddPowerShell7Candidates(List<string> candidates)
        {
            string programW6432 =
                Environment.GetEnvironmentVariable("ProgramW6432");
            string programFiles =
                Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
            string localAppData =
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);

            if (!string.IsNullOrWhiteSpace(programW6432))
            {
                candidates.Add(Path.Combine(
                    programW6432, "PowerShell", "7", "pwsh.exe"
                ));
            }
            if (!string.IsNullOrWhiteSpace(programFiles))
            {
                candidates.Add(Path.Combine(
                    programFiles, "PowerShell", "7", "pwsh.exe"
                ));
            }
            if (!string.IsNullOrWhiteSpace(localAppData))
            {
                candidates.Add(Path.Combine(
                    localAppData, "Programs", "PowerShell", "7", "pwsh.exe"
                ));
            }
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static void ShowError(bool english, string message)
        {
            MessageBox.Show(
                message,
                english ? "WoWS Toolbox startup error" : "WoWS Toolbox 시작 오류",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
        }

        private static void WriteLauncherError(
            string packageRoot,
            Exception error
        )
        {
            try
            {
                string localAppData =
                    Environment.GetFolderPath(
                        Environment.SpecialFolder.LocalApplicationData
                    );
                string stateRoot = Path.Combine(localAppData, "WoWSToolbox");
                Directory.CreateDirectory(stateRoot);
                string logPath = Path.Combine(
                    stateRoot, "launcher-error.log"
                );
                File.AppendAllText(
                    logPath,
                    "[" + DateTimeOffset.Now.ToString("O") + "] " +
                    packageRoot + Environment.NewLine +
                    error + Environment.NewLine + Environment.NewLine,
                    Encoding.UTF8
                );
            }
            catch
            {
                // The message box remains the fallback when logging fails.
            }
        }
    }
}
