import snakes
from snakes.utils.simul.httpd import *
from snakes.utils.simul.html import H
import multiprocessing, time, sys, json, os.path, signal, inspect, glob
import operator

class StateSpace (dict) :
    def __init__ (self, net) :
        self.net = net
        self.current = self.add(net.get_marking())
    def get (self) :
        return self[self.current]
    def add (self, marking) :
        if marking in self :
            return self[marking].num
        else :
            marking.num = len(self) / 2
            self[marking] = self[marking.num] = marking
            self.setmodes(marking.num)
            return marking.num
    def setmodes (self, state) :
        marking = self[state]
        self.net.set_marking(marking)
        marking.modes = []
        for trans in self.net.transition() :
            for mode in trans.modes() :
                marking.modes.append((trans, mode))
    def succ (self, state, mode) :
        marking = self[state]
        trans, binding = marking.modes[mode]
        self.net.set_marking(marking)
        trans.fire(binding)
        self.current = self.add(self.net.get_marking())
        return self.current
    def modes (self, state) :
        return self[state].modes

def log (message) :
    sys.stderr.write("[simulator] %s\n" % message.strip())
    sys.stderr.flush()

shutdown = multiprocessing.Event()
ping = multiprocessing.Event()

class Server (multiprocessing.Process) :
    def __init__ (self, httpd) :
        multiprocessing.Process.__init__(self)
        self.httpd = httpd
    def run (self) :
        try :
            self.httpd.serve_forever()
        except KeyboardInterrupt :
            pass
        finally :
            shutdown.set()

class WatchDog (multiprocessing.Process) :
    def __init__ (self, timeout=30) :
        multiprocessing.Process.__init__(self)
        self.timeout = timeout
    def run (self) :
        try :
            while True :
                if ping.wait(self.timeout) :
                    ping.clear()
                else :
                    log("client not active - %s\n"
                        % time.strftime("%d/%b/%Y %H:%M:%S"))
                    break
        except KeyboardInterrupt :
            pass
        finally :
            shutdown.set()

class BaseHTTPSimulator (Node) :
    def __init__ (self, net, port=8000, respatt=[]) :
        self.res = {}
        dirs = {}
        for cls in reversed(inspect.getmro(self.__class__)[:-2]) :
            path = os.path.dirname(inspect.getsourcefile(cls))
            for pattern in respatt + ["resources/*.js",
                                      "resources/*.css",
                                      "resources/*.html",
                                      "resources/alive.txt"] :
                for res in glob.glob(os.path.join(path, pattern)) :
                    if os.path.isfile(res) :
                        with open(res) as infile :
                            self.res[os.path.basename(res)] = infile.read()
                    elif os.path.isdir(res) :
                        dirs[os.path.basename(res)] = DirNode(res)
                    else :
                        raise ValueError("invalid resource %r" % res)
        Node.__init__(self, r=ResourceNode(self.res, dirs))
        # create HTTP server
        self.port = port
        while True :
            try :
                httpd = HTTPServer(('', self.port), self)
            except Exception as err :
                self.port += 1
            else :
                break
        self.server = Server(httpd)
        self.watchdog = WatchDog()
        # init data
        self.url = "http://127.0.0.1:%s/%s/" % (port, httpd.key)
        self._alive = self.res["alive.txt"].splitlines()
        self._ping = 0
        self.states = StateSpace(net)
    def start (self) :
        log("starting at %r" % self.url)
        shutdown.clear()
        ping.clear()
        self.server.start()
        self.watchdog.start()
    def wait (self) :
        try :
            shutdown.wait()
            log("preparing to shut down...")
            time.sleep(2)
        except KeyboardInterrupt :
            shutdown.set()
        log("shuting down...")
        sig = getattr(signal, "CTRL_C_EVENT",
                      getattr(signal, "SIGTERM", None))
        if sig is not None :
            if self.server.pid :
                os.kill(self.server.pid, sig)
            if self.watchdog.pid :
                os.kill(self.watchdog.pid, sig)
        log("bye!")
    def getstate (self, state) :
        marking = self.states[state]
        places = ["%s = %s" % (H.span(place.name, class_="place"),
                               H.span(marking(place.name), class_="token"))
                  for place in sorted(self.states.net.place(),
                                      key=operator.attrgetter("name"))]
        modes = [{"state" : state,
                  "mode" : i,
                  "html" : "%s : %s" % (H.span(trans.name, class_="trans"),
                                        H.span(binding, class_="binding"))}
                  for i, (trans, binding) in enumerate(marking.modes)]
        return {"id" : state,
                "states" : [{"do" : "sethtml",
                             "select" : "#net",
                             "html" : H.i(self.states.net)},
                            {"do" : "settext",
                             "select" : "#state",
                             "text" : state},
                            {"do" : "setlist",
                             "select" : "#marking",
                             "items" : places},
                            ],
                "modes" : [{"select" : "#modes",
                            "items" : modes},
                           ],
                }
    def init_index (self) :
        return {"res" : "%sr" % self.url,
                "url" : self.url,
                "key" : self.server.httpd.key,
                "host" : "127.0.0.1",
                "port" : self.port,
                "about" : self.init_about(),
                "model" : self.init_model()}
    def init_model (self) :
        return self.res["model.html"]
    def init_about (self) :
        return self.res["about.html"]
    @http("text/html")
    def __call__ (self) :
        return self.res["index.html"] % self.init_index()
    def init_ui (self) :
        argv = H.code(" ".join(sys.argv))
        version = (H.ul(H.li(H.b("Python: "),
                             H.br.join(sys.version.splitlines())),
                        H.li(H.b("SNAKES: "), snakes.version)))
        return [{"label" : "Versions",
                 "id" : "ui-version",
                 "href" : "#",
                 "script" : "dialog(%r)" % version},
                {"label" : "Argv",
                 "id" : "ui-argv",
                 "href" : "#",
                 "script" : "dialog(%r)" % argv}]
    def init_help (self) :
        return {"#trace": "the states and transitions explored so far",
                "#model" : "the model being simulated",
                "#alive .ui #ui-quit" : "stop the simulator (server side)",
                "#alive .ui #ui-help" : "show this help",
                "#alive .ui #ui-about" : "show information about the simulator"}
    @http("application/json", state=int)
    def init (self, state=-1) :
        if state < 0 :
            state = self.states.current
        return {"ui" : self.init_ui(),
                "state" : self.getstate(state),
                "help" : self.init_help()}
    @http("application/json", state=int, mode=int)
    def succ (self, state, mode) :
        state = self.states.succ(state, mode)
        return self.getstate(state)
    @http("text/plain")
    def ping (self) :
        ping.set()
        alive = self._alive[self._ping % len(self._alive)]
        self._ping += 1
        return alive
    @http("text/plain")
    def quit (self) :
        shutdown.set()
        return "Bye!"

if __name__ == "__main__" :
    import snakes.nets, webbrowser
    net = snakes.nets.loads(sys.argv[1])
    simul = BaseHTTPSimulator(net)
    simul.start()
    webbrowser.open(simul.url)
    simul.wait()
