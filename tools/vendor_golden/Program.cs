// Golden-frame generator for the Pawfly / PinYing PY4C protocol tests.
//
// Everything in `Vendor` is copied from the decompiled vendor app (research/decompiled, ILSpy output of
// AquaPinyin.dll) with only the BLE plumbing removed (`await CmdSend(array)` became `return array`) and the
// MAUI `Color` reduced to the two behaviours the app uses (clamped float components, FromRgb(double)).
// The generator writes what the vendor code produces for a spread of inputs, so the Python encoders/decoders
// are checked against the vendor's own arithmetic instead of against a second hand-written copy.
//
//   dotnet run --project tools/vendor_golden -- tests/fixtures/vendor_golden.json
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.Json;

// ---- Microsoft.Maui.Graphics.Color (only what the vendor code touches) --------------------------
sealed class Color
{
    public float Red { get; }
    public float Green { get; }
    public float Blue { get; }

    // Color(float red, float green, float blue): each component .Clamp(0f, 1f)
    public Color(float red, float green, float blue)
    {
        Red = Clamp(red);
        Green = Clamp(green);
        Blue = Clamp(blue);
    }

    static float Clamp(float v) => Math.Min(Math.Max(v, 0f), 1f);

    // FromRgb(double...) -> FromRgba(double...) -> new Color((float)r, (float)g, (float)b, (float)a)
    public static Color FromRgb(double red, double green, double blue) => new Color((float)red, (float)green, (float)blue);
}

// ---- AquaPinyin.Utilities/ColorExtension.cs:15-22 (verbatim) -------------------------------------
static class ColorExtension
{
    public static int ColorToPercentage(float value)
    {
        return Convert.ToInt32(Math.Truncate(value * 100f));
    }

    public static int ColorToPercentage(double value)
    {
        return Convert.ToInt32(Math.Truncate(value));
    }
}

// ---- AquaPinyin.Utilities/Base16Extension.cs:8-17 (verbatim) -------------------------------------
static class Base16Extension
{
    public static byte[] ToHexBytes(this string hex)
    {
        if (string.IsNullOrEmpty(hex))
        {
            return Array.Empty<byte>();
        }
        // the vendor's build binds the .NET 9 Split(ReadOnlySpan<char>) overload; same separators
        return (from s in hex.Split(new char[2] { '-', ' ' })
                where !string.IsNullOrEmpty(s)
                select Convert.ToByte(s, 16)).ToArray();
    }
}

// ---- AquaPinyin.Models/Lamp4CTimer.cs -------------------------------------------------------------
sealed class Lamp4CTimer
{
    public TimeOnly Time { get; set; }
    public Color Color { get; set; }
    public int White { get; set; }
    public int Brightness { get; set; } = 50;

    // emulation-only: the integers the constructor was called with
    public int RawR, RawG, RawB;

    public int Red => ColorExtension.ColorToPercentage(Color.Red);
    public int Green => ColorExtension.ColorToPercentage(Color.Green);
    public int Blue => ColorExtension.ColorToPercentage(Color.Blue);

    public Lamp4CTimer(TimeOnly time, Color color, int white, int brightness)
    {
        Time = time;
        Color = color;
        White = white;
        Brightness = brightness;
    }

    // Lamp4CTimer.cs:36-39 (verbatim)
    public Lamp4CTimer(int hour, int minute, int r, int g, int b, int w, int brightness)
        : this(new TimeOnly(hour, minute), new Color((float)r / 100f, (float)g / 100f, (float)b / 100f), w, brightness)
    {
        RawR = r;
        RawG = g;
        RawB = b;
    }

    // Lamp4CTimer.cs:107-149 (verbatim)
    public static IEnumerable<Lamp4CTimer> Preset(int idx)
    {
        return idx switch
        {
            0 => new Lamp4CTimer[10]
            {
                new Lamp4CTimer(7, 0, 0, 0, 0, 0, 0),
                new Lamp4CTimer(7, 15, 70, 70, 70, 70, 0),
                new Lamp4CTimer(8, 0, 70, 70, 70, 70, 0),
                new Lamp4CTimer(8, 15, 100, 100, 100, 100, 0),
                new Lamp4CTimer(15, 0, 100, 100, 100, 100, 0),
                new Lamp4CTimer(15, 15, 70, 70, 70, 70, 0),
                new Lamp4CTimer(18, 0, 70, 70, 70, 70, 0),
                new Lamp4CTimer(18, 15, 0, 0, 100, 0, 0),
                new Lamp4CTimer(22, 0, 0, 0, 100, 0, 0),
                new Lamp4CTimer(22, 15, 0, 0, 0, 0, 0)
            },
            1 => new Lamp4CTimer[10]
            {
                new Lamp4CTimer(6, 0, 0, 0, 0, 0, 0),
                new Lamp4CTimer(8, 0, 50, 12, 3, 15, 0),
                new Lamp4CTimer(9, 0, 65, 30, 20, 50, 0),
                new Lamp4CTimer(11, 0, 100, 0, 0, 100, 0),
                new Lamp4CTimer(13, 0, 100, 100, 100, 100, 0),
                new Lamp4CTimer(16, 0, 70, 70, 100, 100, 0),
                new Lamp4CTimer(17, 0, 50, 50, 50, 50, 0),
                new Lamp4CTimer(18, 0, 50, 10, 10, 10, 0),
                new Lamp4CTimer(19, 0, 0, 50, 100, 0, 0),
                new Lamp4CTimer(22, 0, 0, 0, 0, 0, 0)
            },
            2 => new Lamp4CTimer[7]
            {
                new Lamp4CTimer(8, 0, 0, 0, 0, 0, 0),
                new Lamp4CTimer(8, 30, 100, 50, 10, 10, 0),
                new Lamp4CTimer(9, 0, 100, 100, 100, 100, 0),
                new Lamp4CTimer(17, 0, 100, 100, 100, 100, 0),
                new Lamp4CTimer(18, 0, 100, 50, 15, 0, 0),
                new Lamp4CTimer(19, 0, 0, 50, 100, 0, 0),
                new Lamp4CTimer(22, 0, 0, 0, 0, 0, 0)
            },
            _ => new Lamp4CTimer[0],
        };
    }
}

// ---- AquaPinyin.Services (BaseDevice.cs / FourChannel.cs), frame builders --------------------------
static class Vendor
{
    // BaseDevice.cs:278-286 (verbatim)
    public static byte CheckSum(params byte[] data)
    {
        int num = 0;
        for (int i = 0; i < data.Length; i++)
        {
            num = (num + data[i]) % 65535;
        }
        return Convert.ToByte(num & 0xFF);
    }

    // BaseDevice.cs:206-225 (KeySend, frame part)
    public static byte[] KeyFrame(string k, bool update)
    {
        k = k.PadRight(8, '0');
        object[] buffer = new object[8];
        for (int i = 0; i < 8; i++)
        {
            buffer[i] = k[i];
        }
        string hex = string.Format("{6}{7} {4}{5} {2}{3} {0}{1}", buffer);
        List<byte> list = new List<byte>((!update) ? new byte[3] { 189, 6, 10 } : new byte[3] { 173, 6, 32 });
        list.AddRange(hex.ToHexBytes());
        list.Add(255);
        list.Add(CheckSum(list[0], list[1], list[2], list[3], list[4], list[5], list[6], list[7]));
        return list.ToArray();
    }

    // BaseDevice.cs:119-147 (Rename, frame part; Encoding.Default is UTF-8 on .NET/Android)
    public static byte[] RenameFrame(string name)
    {
        string newname = name ?? "";
        byte[] array = Encoding.Default.GetBytes(newname);
        if (array.Length > 16)
        {
            array = array.Take(16).ToArray();
        }
        List<byte> list = new List<byte>();
        list.Add(173);
        list.Add(Convert.ToByte(array.Length + 1));
        list.Add(33);
        list.AddRange(array);
        list.Add(CheckSum(list.ToArray()));
        return list.ToArray();
    }

    // FourChannel.cs:134-164 (AdjPower): the app toggles, so `power` is the state it currently believes.
    public static byte[] AdjPower(bool power)
    {
        byte[] array = new byte[9]
        {
            173, 6, 1, Convert.ToByte((!power) ? 1 : 0), 0, 0, 0, 0, 0
        };
        byte b;
        array[7] = (b = 255);
        array[4] = (array[5] = (array[6] = b));
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        return array;
    }

    // FourChannel.cs:172-195 (AdjPwm; AdjWhite calls it with chl = 3)
    public static byte[] AdjPwm(int value, int chl = 3)
    {
        byte[] array = new byte[9]
        {
            173, 6, 4, Convert.ToByte(value), Convert.ToByte(chl), 0, 0, 0, 0
        };
        byte b;
        array[7] = (b = 255);
        array[5] = (array[6] = b);
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        return array;
    }

    // FourChannel.cs:197-222 (AdjColor)
    public static byte[] AdjColor(Color value)
    {
        Color Color = value;
        byte[] array = new byte[9]
        {
            173,
            6,
            5,
            Convert.ToByte(ColorExtension.ColorToPercentage(Color.Red)),
            Convert.ToByte(ColorExtension.ColorToPercentage(Color.Green)),
            Convert.ToByte(ColorExtension.ColorToPercentage(Color.Blue)),
            0,
            0,
            0
        };
        byte b;
        array[7] = (b = 255);
        array[6] = b;
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        return array;
    }

    // FourChannel.cs:224-249 (AdjBrightness)
    public static byte[] AdjBrightness(int value)
    {
        byte[] array = new byte[9]
        {
            173, 6, 2, Convert.ToByte(value), 0, 0, 0, 0, 0
        };
        byte b;
        array[7] = (b = 255);
        array[4] = (array[5] = (array[6] = b));
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        return array;
    }

    // FourChannel.cs:251-276 (AdjSpeed; never called by any page of the app)
    public static byte[] AdjSpeed(int value)
    {
        byte[] array = new byte[9]
        {
            173, 6, 3, Convert.ToByte(value), 0, 0, 0, 0, 0
        };
        byte b;
        array[7] = (b = 255);
        array[4] = (array[5] = (array[6] = b));
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        return array;
    }

    // FourChannel.cs:278-338 (AdjScenario, frame part)
    public static byte[] AdjScenario(int value)
    {
        byte[] array = new byte[9]
        {
            173, 6, 7, Convert.ToByte(value), 0, 0, 0, 0, 0
        };
        byte b;
        array[7] = (b = 255);
        array[4] = (array[5] = (array[6] = b));
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        return array;
    }

    // FourChannel.cs:340-365 (AdjTimer): DIY ids 3..5 go out as id + 125
    public static byte[] AdjTimer(int id)
    {
        byte[] array = new byte[9]
        {
            173, 6, 8, Convert.ToByte((id <= 2) ? id : (id + 125)), 0, 0, 0, 0, 0
        };
        byte b;
        array[7] = (b = 255);
        array[4] = (array[5] = (array[6] = b));
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        return array;
    }

    // FourChannel.cs:367-380 (AdjDemo)
    public static byte[] AdjDemo()
    {
        byte[] array = new byte[9] { 173, 6, 11, 0, 0, 0, 0, 0, 0 };
        byte b;
        array[7] = (b = 255);
        array[3] = (array[4] = (array[5] = (array[6] = b)));
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        return array;
    }

    // FourChannel.cs:382-401 (AdjTime), with DateTime.Now replaced by the argument
    public static byte[] AdjTime(DateTime now)
    {
        byte b = (byte)((now.DayOfWeek == DayOfWeek.Sunday) ? 7 : Convert.ToByte(now.DayOfWeek));
        byte[] array = new byte[9];
        array[0] = 173;
        array[1] = 6;
        array[2] = 6;
        array[3] = Convert.ToByte(now.Hour);
        array[4] = Convert.ToByte(now.Minute);
        array[5] = Convert.ToByte(now.Second);
        array[6] = b;
        array[7] = 255;
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        return array;
    }

    // FourChannel.cs:403-449 (AdjDiy): header frame, then one frame per point sorted by time
    public static List<byte[]> AdjDiy(int id, Lamp4CTimer[] timer = null)
    {
        List<byte[]> frames = new List<byte[]>();
        byte[] array = new byte[9];
        array[0] = 173;
        array[1] = 6;
        array[2] = 9;
        array[3] = Convert.ToByte((id <= 2) ? id : (id + 125));
        array[4] = Convert.ToByte((timer != null) ? timer.Length : 0);
        array[5] = 0;
        array[6] = 127;
        array[7] = 255;
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        frames.Add(array);
        if (timer != null && timer.Length != 0)
        {
            byte ord = 0;
            foreach (Lamp4CTimer item in timer.OrderBy((Lamp4CTimer s) => s.Time))
            {
                array = new byte[11];
                array[0] = 173;
                array[1] = 8;
                array[2] = 10;
                array[3] = ord;
                array[4] = Convert.ToByte(item.Time.Hour);
                array[5] = Convert.ToByte(item.Time.Minute);
                array[6] = Convert.ToByte(item.Color.Red * 100f);
                array[7] = Convert.ToByte(item.Color.Green * 100f);
                array[8] = Convert.ToByte(item.Color.Blue * 100f);
                array[9] = Convert.ToByte(item.White);
                array[10] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7], array[8], array[9]);
                ord++;
                frames.Add(array);
            }
        }
        return frames;
    }

    // FourChannel.cs:451-473 (AdjForDiy)
    public static byte[] AdjForDiy(Color value, int white)
    {
        byte[] array = new byte[9] { 173, 6, 12, 0, 0, 0, 0, 0, 0 };
        if (value != null)
        {
            array[3] = 1;
            array[4] = Convert.ToByte(ColorExtension.ColorToPercentage(value.Red));
            array[5] = Convert.ToByte(ColorExtension.ColorToPercentage(value.Green));
            array[6] = Convert.ToByte(ColorExtension.ColorToPercentage(value.Blue));
            array[7] = Convert.ToByte(white);
        }
        else
        {
            array[3] = (array[4] = (array[5] = (array[6] = (array[7] = 0))));
        }
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        return array;
    }

    // FourChannel.cs:475-494 (QueryDiy)
    public static byte[] QueryDiy(int id)
    {
        byte[] array = new byte[9]
        {
            189, 6, 2, Convert.ToByte((id <= 2) ? id : (id + 125)), 0, 0, 0, 0, 0
        };
        byte b;
        array[7] = (b = 255);
        array[4] = (array[5] = (array[6] = b));
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        return array;
    }

    // FourChannel.cs:496-504 (QueryStatus)
    public static byte[] QueryStatus()
    {
        byte[] array = new byte[9] { 189, 6, 1, 0, 0, 0, 0, 0, 0 };
        byte b;
        array[7] = (b = 255);
        array[3] = (array[4] = (array[5] = (array[6] = b)));
        array[8] = CheckSum(array[0], array[1], array[2], array[3], array[4], array[5], array[6], array[7]);
        return array;
    }
}

// ---- receive side: BaseDevice.OnValueChanged (BaseDevice.cs:242-269) + FourChannel.OnDataReceived (58-132) ----
sealed class Device
{
    public bool Power;
    public int Brightness, Speed, WorkMode, TimerId = -1, Scenario = -1, White;
    public Color Color = new Color(0f, 0f, 0f);
    public int RawR, RawG, RawB;

    List<Lamp4CTimer> _diys;
    int _diyCount;
    int _diid;
    public readonly List<(int id, List<Lamp4CTimer> timers)> DiyEvents = new List<(int, List<Lamp4CTimer>)>();
    public readonly List<bool> KeyEvents = new List<bool>();

    // BaseDevice.cs:242-269
    public bool OnValueChanged(byte[] value)
    {
        if (value == null || value.Length == 0)
        {
            return false;
        }
        if (value[0] == 189 && value[1] == 6 && value[2] == 138 && value.Length >= 4)
        {
            KeyEvents.Add(value[3] == 1);
            return false;
        }
        return OnDataReceived(value);
    }

    // FourChannel.cs:58-132 (verbatim apart from the event plumbing)
    public bool OnDataReceived(byte[] data)
    {
        bool result = false;
        if (data != null && data.Length > 4)
        {
            int num = Array.IndexOf(data, Convert.ToByte(189));
            if (num >= 0)
            {
                switch (data[num + 2])
                {
                    case 129:
                        if (data.Length >= 13)
                        {
                            Power = Convert.ToInt32(data[num + 3]) != 0;
                            Brightness = data[num + 4];
                            Speed = data[num + 5];
                            WorkMode = data[num + 6];
                            if (WorkMode == 1)
                            {
                                Scenario = data[num + 7];
                            }
                            else if (WorkMode == 2)
                            {
                                int num3 = Convert.ToInt32(data[num + 7]);
                                TimerId = ((num3 >= 128) ? (num3 - 125) : num3);
                            }
                            byte b2 = data[num + 8];
                            byte b3 = data[num + 9];
                            byte b4 = data[num + 10];
                            RawR = b2;
                            RawG = b3;
                            RawB = b4;
                            Color = new Color((float)(int)b2 / 100f, (float)(int)b3 / 100f, (float)(int)b4 / 100f);
                            White = data[num + 11];
                            result = true;
                        }
                        break;
                    case 130:
                        if (data.Length >= 9)
                        {
                            _diid = Convert.ToInt32(data[num + 3]);
                            _diyCount = Convert.ToInt32(data[num + 4]);
                            _diys = new List<Lamp4CTimer>(_diyCount);
                            if (_diyCount == 0)
                            {
                                DiyEvents.Add((_diid, _diys));
                                _diys = null;
                                _diyCount = 0;
                                _diid = 0;
                            }
                        }
                        break;
                    case 131:
                        if (data.Length >= 11 && _diys != null)
                        {
                            TimeOnly time = new TimeOnly(data[num + 4], data[num + 5]);
                            byte num2 = data[num + 6];
                            byte b = data[num + 7];
                            Color color = new Color(blue: (float)(int)data[num + 8] / 100f, red: (float)(int)num2 / 100f, green: (float)(int)b / 100f);
                            byte white = data[num + 9];
                            var t = new Lamp4CTimer(time, color, white, 0);
                            t.RawR = num2;
                            t.RawG = b;
                            t.RawB = data[num + 8];
                            _diys.Add(t);
                            if (_diyCount == _diys.Count)
                            {
                                DiyEvents.Add((_diid, _diys));
                                _diys = null;
                                _diyCount = 0;
                                _diid = 0;
                            }
                        }
                        break;
                    default:
                        result = false;
                        break;
                }
            }
        }
        return result;
    }
}

// ---- AquaPinyin.Services/BaseDevice.cs:16, 49-76 (verbatim) ---------------------------------------
static class NameRules
{
    public static readonly string[] AdvertisedName = new string[2] { "PY4C", "PYLamp-4C" };

    public static string DisplayName(string name)
    {
        int deviceType = GetDeviceType(name);
        if (deviceType >= 0)
        {
            int length = AdvertisedName[deviceType].Length;
            if (name.Length > length)
            {
                return name.Substring(length).TrimStart('-', '_');
            }
        }
        return name;
    }

    public static int GetDeviceType(string name)
    {
        if (!string.IsNullOrEmpty(name))
        {
            for (int i = 0; i < AdvertisedName.Length; i++)
            {
                if (name.StartsWith(AdvertisedName[i]))
                {
                    return i;
                }
            }
        }
        return -1;
    }
}

static class Program
{
    static string Hex(byte[] b) => Convert.ToHexString(b).ToLowerInvariant();

    static byte[] FromHex(string s) => Convert.FromHexString(s.Replace(" ", ""));

    // Builds `AD/BD frame` bytes for a reply: [family, len, cmd, payload..., checksum]
    static byte[] Reply(byte cmd, params byte[] payload)
    {
        List<byte> l = new List<byte> { 189, (byte)(payload.Length + 1), cmd };
        l.AddRange(payload);
        l.Add(Vendor.CheckSum(l.ToArray()));
        return l.ToArray();
    }

    static Dictionary<string, object> Point(Lamp4CTimer t) => new Dictionary<string, object>
    {
        ["hour"] = t.Time.Hour,
        ["minute"] = t.Time.Minute,
        ["red"] = t.RawR,
        ["green"] = t.RawG,
        ["blue"] = t.RawB,
        ["white"] = t.White,
    };

    static Dictionary<string, object> ParseOne(string name, byte[] frame)
    {
        Device dev = new Device();
        Dictionary<string, object> o = new Dictionary<string, object> { ["name"] = name, ["hex"] = Hex(frame) };
        try
        {
            bool handled = dev.OnValueChanged(frame);
            if (dev.KeyEvents.Count > 0)
            {
                o["kind"] = "key";
                o["ok"] = dev.KeyEvents[0];
            }
            else if (handled)
            {
                o["kind"] = "status";
                o["power"] = dev.Power;
                o["brightness"] = dev.Brightness;
                o["speed"] = dev.Speed;
                o["work_mode"] = dev.WorkMode;
                o["scenario"] = dev.Scenario;
                o["timer_id"] = dev.TimerId;
                o["red"] = dev.RawR;
                o["green"] = dev.RawG;
                o["blue"] = dev.RawB;
                o["white"] = dev.White;
            }
            else if (dev.DiyEvents.Count > 0)
            {
                o["kind"] = "program";
                o["id"] = dev.DiyEvents[0].id;
                o["points"] = dev.DiyEvents[0].timers.Select(Point).ToList();
            }
            else
            {
                o["kind"] = "none";
            }
        }
        catch (Exception ex)
        {
            o["kind"] = "exception";
            o["exception"] = ex.GetType().Name;
        }
        return o;
    }

    // Feed a sequence of notifications to one device object (a program query = header + point frames).
    static Dictionary<string, object> ParseSequence(string name, IEnumerable<byte[]> frames)
    {
        Device dev = new Device();
        foreach (byte[] f in frames)
        {
            dev.OnValueChanged(f);
        }
        return new Dictionary<string, object>
        {
            ["name"] = name,
            ["frames"] = frames.Select(Hex).ToList(),
            ["events"] = dev.DiyEvents.Select(e => new Dictionary<string, object>
            {
                ["id"] = e.id,
                ["points"] = e.timers.Select(Point).ToList(),
            }).ToList(),
        };
    }

    static void Main(string[] args)
    {
        var root = new Dictionary<string, object>();
        root["generator"] = "tools/vendor_golden (verbatim vendor frame builders + receive path, .NET " + Environment.Version + ")";

        // ---- checksum -------------------------------------------------------------------------
        var sums = new List<Dictionary<string, object>>();
        foreach (byte[] data in new[]
        {
            new byte[0], new byte[] { 0 }, new byte[] { 255 }, new byte[] { 255, 255 }, new byte[] { 189, 6, 1, 255, 255, 255, 255, 255 },
            new byte[] { 173, 6, 1, 1, 255, 255, 255, 255 }, Enumerable.Repeat((byte)255, 257).ToArray(), Enumerable.Repeat((byte)255, 258).ToArray(),
            Enumerable.Repeat((byte)255, 300).ToArray(),
        })
        {
            sums.Add(new Dictionary<string, object> { ["data"] = Hex(data), ["checksum"] = (int)Vendor.CheckSum(data) });
        }
        root["checksum"] = sums;

        // ---- keys -----------------------------------------------------------------------------
        var keys = new List<Dictionary<string, object>>();
        foreach (string k in new[] { "12345678", "00000000", "99999999", "87654321", "10203040", "00000001", "90000000" })
        {
            keys.Add(new Dictionary<string, object>
            {
                ["key"] = k,
                ["verify"] = Hex(Vendor.KeyFrame(k, update: false)),
                ["change"] = Hex(Vendor.KeyFrame(k, update: true)),
            });
        }
        root["keys"] = keys;

        // ---- rename ---------------------------------------------------------------------------
        var names = new List<Dictionary<string, object>>();
        foreach (string n in new[] { "A", "BT-PSTL", "Tank", "0123456789ABCDEF", "a b", "Plant Room 1", "Z9_-." })
        {
            names.Add(new Dictionary<string, object> { ["name"] = n, ["hex"] = Hex(Vendor.RenameFrame(n)) });
        }
        root["rename"] = names;

        var display = new List<Dictionary<string, object>>();
        foreach (string n in new[] { "PY4C-BT-PSTL", "PYLamp-4C_Tank", "PYLamp-4C", "PY4C", "PY4C-", "PY4C_-x", "PY4C-PY4C-a", "Other", "", "py4c-lower", "PY4CXYZ", "PY4C - spaced", "BT-PSTL" })
        {
            display.Add(new Dictionary<string, object> { ["advertised"] = n, ["display"] = NameRules.DisplayName(n) });
        }
        root["display_name"] = display;

        // ---- simple frames --------------------------------------------------------------------
        root["query_status"] = Hex(Vendor.QueryStatus());
        root["query_program"] = Enumerable.Range(0, 6).Select(i => new Dictionary<string, object> { ["program"] = i, ["hex"] = Hex(Vendor.QueryDiy(i)) }).ToList();
        root["power"] = new Dictionary<string, object>
        {
            // the app sends "not currently on": from off it sends 1, from on it sends 0
            ["on"] = Hex(Vendor.AdjPower(false)),
            ["off"] = Hex(Vendor.AdjPower(true)),
        };
        root["brightness"] = Enumerable.Range(0, 101).Select(v => new Dictionary<string, object> { ["value"] = v, ["hex"] = Hex(Vendor.AdjBrightness(v)) }).ToList();
        root["speed"] = new[] { 0, 1, 2, 50, 100, 127, 128, 200, 255 }.Select(v => new Dictionary<string, object> { ["value"] = v, ["hex"] = Hex(Vendor.AdjSpeed(v)) }).ToList();
        root["white"] = Enumerable.Range(0, 101).Select(v => new Dictionary<string, object> { ["value"] = v, ["hex"] = Hex(Vendor.AdjPwm(v)) }).ToList();
        var channels = new List<Dictionary<string, object>>();
        foreach (int chl in new[] { 0, 1, 2, 3 })
        {
            foreach (int v in new[] { 0, 1, 33, 60, 100 })
            {
                channels.Add(new Dictionary<string, object> { ["channel"] = chl, ["value"] = v, ["hex"] = Hex(Vendor.AdjPwm(v, chl)) });
            }
        }
        root["channel"] = channels;
        root["scenario"] = Enumerable.Range(0, 8).Select(i => new Dictionary<string, object> { ["index"] = i, ["hex"] = Hex(Vendor.AdjScenario(i)) }).ToList();
        root["program"] = Enumerable.Range(0, 6).Select(i => new Dictionary<string, object> { ["program"] = i, ["hex"] = Hex(Vendor.AdjTimer(i)) }).ToList();
        root["demo"] = Hex(Vendor.AdjDemo());

        // ---- time sync ------------------------------------------------------------------------
        var times = new List<Dictionary<string, object>>();
        foreach (var dt in new[]
        {
            new DateTime(2026, 9, 28, 0, 0, 0),   // Monday
            new DateTime(2026, 9, 29, 7, 5, 9),   // Tuesday
            new DateTime(2026, 9, 30, 1, 8, 29),  // Wednesday (a live test)
            new DateTime(2026, 10, 1, 13, 59, 59), // Thursday
            new DateTime(2026, 10, 2, 23, 59, 59), // Friday
            new DateTime(2026, 10, 3, 12, 0, 0),  // Saturday
            new DateTime(2026, 10, 4, 6, 30, 15), // Sunday
        })
        {
            times.Add(new Dictionary<string, object>
            {
                ["iso"] = dt.ToString("yyyy-MM-ddTHH:mm:ss"),
                ["hex"] = Hex(Vendor.AdjTime(dt)),
            });
        }
        root["time_sync"] = times;

        // ---- the app's slider-to-byte arithmetic (quirk report) ---------------------------------
        // Colour path 1: palette slider value v (double, 0..100) -> Color.FromRgb(v / 100.0) -> ColorToPercentage
        // Colour path 2: a Lamp4CTimer colour built from an int percent -> ColorToPercentage / Convert.ToByte(*100f)
        var quirk = new Dictionary<string, object>();
        var slider = new List<int>();
        var timerTruncated = new List<int>();
        var timerRounded = new List<int>();
        for (int v = 0; v <= 100; v++)
        {
            Color viaSlider = Color.FromRgb(v / 100.0, 0, 0);
            if (ColorExtension.ColorToPercentage(viaSlider.Red) != v)
            {
                slider.Add(v);
            }
            Color viaTimer = new Color((float)v / 100f, 0f, 0f);
            if (ColorExtension.ColorToPercentage(viaTimer.Red) != v)
            {
                timerTruncated.Add(v);
            }
            if (Convert.ToByte(viaTimer.Red * 100f) != v)
            {
                timerRounded.Add(v);
            }
        }
        quirk["slider_percent_sent_off_by_one"] = slider;
        quirk["timer_percent_displayed_off_by_one"] = timerTruncated;
        quirk["timer_percent_uploaded_off_by_one"] = timerRounded;
        root["vendor_quirks"] = quirk;

        // ---- live colour frames: only the percent values the app maps back to themselves ------------
        var colors = new List<Dictionary<string, object>>();
        var triples = new List<(int, int, int)> { (0, 0, 0), (100, 100, 100), (0, 100, 0), (100, 0, 0), (0, 0, 100), (37, 62, 81), (10, 90, 0), (1, 2, 3), (99, 50, 49), (0, 10, 100) };
        for (int v = 0; v <= 100; v++)
        {
            triples.Add((v, 0, 0));
            triples.Add((0, v, 0));
            triples.Add((0, 0, v));
        }
        foreach (var (r, g, b) in triples.Distinct())
        {
            Color c = new Color((float)r / 100f, (float)g / 100f, (float)b / 100f);
            byte[] frame = Vendor.AdjColor(c);
            colors.Add(new Dictionary<string, object>
            {
                ["red"] = r,
                ["green"] = g,
                ["blue"] = b,
                ["hex"] = Hex(frame),
                ["exact"] = frame[3] == r && frame[4] == g && frame[5] == b,
            });
        }
        root["color"] = colors;

        // ---- preview (AdjForDiy) --------------------------------------------------------------
        var previews = new List<Dictionary<string, object>>();
        foreach (var (r, g, b, w) in new[] { (0, 0, 0, 0), (100, 100, 100, 100), (0, 0, 100, 0), (100, 0, 0, 0), (37, 62, 81, 5), (1, 2, 3, 4), (0, 100, 0, 0) })
        {
            Color c = new Color((float)r / 100f, (float)g / 100f, (float)b / 100f);
            byte[] frame = Vendor.AdjForDiy(c, w);
            previews.Add(new Dictionary<string, object>
            {
                ["rgbw"] = new[] { r, g, b, w },
                ["hex"] = Hex(frame),
                ["exact"] = frame[4] == r && frame[5] == g && frame[6] == b,
            });
        }
        root["preview"] = previews;
        root["preview_end"] = Hex(Vendor.AdjForDiy(null, -1));

        // ---- program upload -------------------------------------------------------------------
        var uploads = new List<Dictionary<string, object>>();
        void AddUpload(string name, int idx, Lamp4CTimer[] pts)
        {
            uploads.Add(new Dictionary<string, object>
            {
                ["name"] = name,
                ["program"] = idx,
                ["points"] = pts.Select(Point).ToList(),
                ["frames"] = Vendor.AdjDiy(idx, pts).Select(Hex).ToList(),
            });
        }
        AddUpload("empty", 3, new Lamp4CTimer[0]);
        AddUpload("one point", 4, new[] { new Lamp4CTimer(12, 30, 100, 50, 10, 20, 0) });
        AddUpload("live test 3 points", 3, new[]
        {
            new Lamp4CTimer(6, 0, 0, 0, 0, 0, 0), new Lamp4CTimer(12, 30, 100, 50, 10, 20, 0), new Lamp4CTimer(23, 59, 5, 6, 7, 8, 0),
        });
        AddUpload("unsorted input", 5, new[]
        {
            new Lamp4CTimer(22, 0, 1, 2, 3, 4, 0), new Lamp4CTimer(0, 0, 100, 100, 100, 100, 0), new Lamp4CTimer(9, 45, 0, 100, 0, 0, 0),
        });
        for (int p = 0; p < 3; p++)
        {
            AddUpload("preset " + p + " as DIY 1", 3, Lamp4CTimer.Preset(p).ToArray());
            AddUpload("preset " + p + " as DIY 3", 5, Lamp4CTimer.Preset(p).ToArray());
        }
        var twelve = new List<Lamp4CTimer>();
        int[][] rows =
        {
            new[] { 0, 30, 0, 0, 0, 0 }, new[] { 2, 0, 10, 20, 30, 40 }, new[] { 4, 15, 100, 0, 0, 0 }, new[] { 6, 45, 0, 100, 0, 0 },
            new[] { 8, 0, 0, 0, 100, 0 }, new[] { 10, 10, 0, 0, 0, 100 }, new[] { 12, 0, 100, 100, 100, 100 }, new[] { 14, 20, 50, 25, 12, 6 },
            new[] { 16, 59, 1, 2, 3, 4 }, new[] { 19, 5, 99, 98, 97, 96 }, new[] { 21, 30, 33, 66, 99, 0 }, new[] { 23, 59, 0, 0, 0, 0 },
        };
        foreach (int[] r in rows)
        {
            twelve.Add(new Lamp4CTimer(r[0], r[1], r[2], r[3], r[4], r[5], 0));
        }
        AddUpload("twelve points", 4, twelve.ToArray());
        // every percent 0..100 goes through Convert.ToByte(float * 100f) (the upload rounding path)
        var sweep = new List<Lamp4CTimer>();
        for (int v = 0; v <= 100; v += 10)
        {
            sweep.Add(new Lamp4CTimer(v / 10, v % 60, v, 100 - v, v, 100 - v, 0));
        }
        AddUpload("percent sweep", 3, sweep.ToArray());
        root["program_upload"] = uploads;

        // ---- presets as the app draws them ----------------------------------------------------
        var presets = new Dictionary<string, object>();
        for (int p = 0; p < 3; p++)
        {
            presets[p.ToString()] = Lamp4CTimer.Preset(p).Select(Point).ToList();
        }
        root["presets"] = presets;

        // ---- receive path ---------------------------------------------------------------------
        var parse = new List<Dictionary<string, object>>();
        // live frames (real light) ...
        foreach (var (name, hex) in new[]
        {
            ("live: manual green 90% (SPEC)", "bd0a81015a000007006400000e"),
            ("live: power off", "bd0a81005a000007006400000d"),
            ("live: scenario 3", "bd0a81015a000103006400000b"),
            ("live: program 0", "bd0a81015a0002000064000009"),
            ("live: DIY 2 running (sel 0x81)", "bd0a81015a000281006400008a"),
            ("live: DIY 3 running (sel 0x82)", "bd0a81015a000282006400008b"),
            ("live: DIY 1 running (sel 0x80)", "bd0a81015a0002800064000089"),
            ("live: colour 37/62/81", "bd0a81015a000007253e51005e"),
            ("live: brightness 0", "bd0a81010000000700640000b4"),
            ("live: white 60", "bd0a81015a0000000000003cdf"),
            ("live: key ok", "bd068a01ffffffff4a"),
        })
        {
            parse.Add(ParseOne(name, FromHex(hex)));
        }
        // ... and synthetic ones for every branch of the vendor parser
        foreach (var (name, frame) in new[]
        {
            ("synthetic: power 1 bright 100 speed 9 mode 0", Reply(0x81, 1, 100, 9, 0, 0, 100, 50, 25, 12)),
            ("synthetic: scenario 7", Reply(0x81, 1, 50, 0, 1, 7, 1, 2, 3, 4)),
            ("synthetic: program 2", Reply(0x81, 1, 50, 0, 2, 2, 1, 2, 3, 4)),
            ("synthetic: program sel 127", Reply(0x81, 1, 50, 0, 2, 127, 1, 2, 3, 4)),
            ("synthetic: program sel 128", Reply(0x81, 1, 50, 0, 2, 128, 1, 2, 3, 4)),
            ("synthetic: program sel 130", Reply(0x81, 1, 50, 0, 2, 130, 1, 2, 3, 4)),
            ("synthetic: mode 3 (unknown)", Reply(0x81, 1, 50, 0, 3, 5, 1, 2, 3, 4)),
            ("synthetic: power byte 2", Reply(0x81, 2, 50, 0, 0, 5, 1, 2, 3, 4)),
            ("synthetic: key refused", Reply(0x8A, 0, 255, 255, 255, 255)),
            ("synthetic: unknown reply 0x90", Reply(0x90, 1, 2, 3, 4, 5, 6)),
            ("synthetic: status frame too short", Reply(0x81, 1, 2, 3)),
        })
        {
            parse.Add(ParseOne(name, frame));
        }
        root["parse"] = parse;

        // program queries: header, then the announced number of points
        var seqs = new List<Dictionary<string, object>>();
        seqs.Add(ParseSequence("empty DIY 1 (live)", new[] { FromHex("bd06828000007f074b") }));
        seqs.Add(ParseSequence("3 points DIY 1 (live)", new[]
        {
            FromHex("bd06828003007fff46"), FromHex("bd0883000600000000004e"), FromHex("bd0883010c1e64320a1427"), FromHex("bd088302173b05060708b6"),
        }));
        // a real 12-point read-back captured from the light (upload at 100 ms gaps, random colours)
        seqs.Add(ParseSequence("12 points DIY 1 (live readback)", new[]
        {
            FromHex("bd0682800c007f3585"),
            FromHex("bd088300003b0f303d201f"), FromHex("bd088301012e31460e4a47"), FromHex("bd088302051520025e1c00"), FromHex("bd088303081e3524186345"),
            FromHex("bd088304082b3215620a32"), FromHex("bd0883050c0e1250503952"), FromHex("bd0883060f3511110101b6"), FromHex("bd08830712051b641c1617"),
            FromHex("bd088308150f1626291af3"), FromHex("bd08830916094657511b79"), FromHex("bd08830a160f18591a3234"), FromHex("bd08830b172227032f361b"),
        }));
        // the twelve-point upload sample above, answered the way the light answers (header, then points in order)
        var answer = new List<byte[]> { Reply(0x82, 0x81, 12, 0, 127, 0) };
        for (int i = 0; i < rows.Length; i++)
        {
            answer.Add(Reply(0x83, (byte)i, (byte)rows[i][0], (byte)rows[i][1], (byte)rows[i][2], (byte)rows[i][3], (byte)rows[i][4], (byte)rows[i][5]));
        }
        seqs.Add(ParseSequence("12 points DIY 2 (generated answer)", answer));
        seqs.Add(ParseSequence("points before any header are ignored", new[] { FromHex("bd0883000600000000004e") }));
        seqs.Add(ParseSequence("header restarts collection", new[]
        {
            Reply(0x82, 0x80, 2, 0, 127, 7), Reply(0x83, 0, 6, 0, 1, 2, 3, 4), Reply(0x82, 0x81, 1, 0, 127, 7), Reply(0x83, 0, 9, 30, 5, 6, 7, 8),
        }));
        root["parse_sequences"] = seqs;

        string json = JsonSerializer.Serialize(root, new JsonSerializerOptions { WriteIndented = true });
        if (args.Length > 0)
        {
            File.WriteAllText(args[0], json + "\n");
        }
        else
        {
            Console.WriteLine(json);
        }
    }
}
