# tsproxy
Traffic-shaping SOCKS5 proxy

tsproxy provides basic latency, download and upload traffic shaping while only requiring user-level access (no root permissions required).  It should work for basic browser testing but for protocol-level work it does not provide a suitable replacement for something like dummynet or netem.

tsproxy is monolithic and all of the functionality is in tsproxy.py.  It is written expecting Python 2.7.

#Usage
```bash
$ python tsproxy.py --rtt=<latency> --inkbps=<download bandwidth> --outkbps=<upload bandwidth>
```
Hit ctrl-C (or send a SIGINT) to exit

#Example
```bash
$ python tsproxy.py --rtt=200 --inkbps=1600 --outkbps=768
```

#Command-line Options
* **-r, --rtt** : Latency in milliseconds (full round trip, half of the latency gets applied to each direction).
* **-i, --inkbps** : Download Bandwidth (in 1000 bits/s - Kbps).
* **-o, --outkbps** : Upload Bandwidth (in 1000 bits/s - Kbps).
* **-w, --window** : Emulated TCP initial congestion window (defaults to 10).
* **-p, --port** : SOCKS 5 proxy port (defaults to port 1080).
* **-b, --bind** : Interface address to listen on (defaults to localhost).
* **-d, --desthost** : Redirect all outbound connections to the specified host (name or IP).
* **-v, --verbose** : Increase verbosity (specify multiple times for more). -vvvv for full debug output.

#Configuring Chrome to use tsproxy
Add a --proxy-server command-line option.
```
--proxy-server="socks://localhost:1080"
```

#Known Shortcomings/Issues
* The proxy reads all available data from the browser/server and applies traffic-shaping before sending it through.  That means that:
  * uploading/sending data will appear to go out from the browser far faster than it actually gets through to the server (completing the upload will still take the same amount of time).
  * Resources being downloaded may stack up behind each other instead of sharing the available bandwidth (no tcp flow control)
* DNS lookups on OSX (and FreeBSD) will block each other when it comes to actually resolving.  DNS in Python on most platforms is allowed to run concurrently in threads (which tsproxy does) but on OSX and FreeBSD it is not thread-safe and there is a lock around the actual lookups.  For most cases this isn't an issue because the latency isn't added on the actual DNS lookup (it is from the browser perspective but it is added outside of the actual lookup)
