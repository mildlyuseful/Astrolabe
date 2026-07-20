using System;
using System.IO;
using System.Text.Json;

namespace TrackballNav
{
    internal static class BrokerConfig
    {
        internal const int DefaultPort = 47900;

        internal static string DefaultPath => Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "TrackballDaemon", "bridge.json");

        internal static int ResolvePort(string path = null)
        {
            try
            {
                using var document = JsonDocument.Parse(File.ReadAllText(path ?? DefaultPath));
                if (document.RootElement.ValueKind == JsonValueKind.Object &&
                    document.RootElement.TryGetProperty("port", out var value) &&
                    value.ValueKind == JsonValueKind.Number &&
                    value.TryGetInt32(out int port) &&
                    port >= 1 && port <= 65535)
                    return port;
            }
            catch
            {
                // Missing, unreadable, or malformed discovery data uses the protocol default.
            }
            return DefaultPort;
        }
    }
}
