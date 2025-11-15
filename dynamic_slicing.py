from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, udp
from ryu.lib import hub
import time


class SliceEnforcingController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    # Porte verso backbone
    PORT_S2 = 3  # Porta verso S2
    PORT_S3 = 4  # Porta verso S3
    QUEUE_LOW  = 1  # coda bassa priorità per non-video
    QUEUE_HIGH = 0  # coda alta priorità per video

    # Host collegati a S1 e S4
    HOST_PORTS = {
        1: [1,2],  # S1: h1, h2
        4: [1,2],  # S4: h3, h4
    }

    PRIORITY_DEFAULT = 10
    PRIORITY_VIDEO   = 20

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}

        # Monitor per traffico video
        self._video_bytes   = 0
        self._last_measure  = time.time()
        self.allow_non_video_upper = True
        self._monitor_thread = hub.spawn(self._monitor)

    def _monitor(self):
        while True:
            now = time.time()
            elapsed = now - self._last_measure
            if elapsed > 0:
                video_mbps = (self._video_bytes * 8.0) / 1e6 / elapsed
                self._video_bytes = 0
                self._last_measure = now
                self.allow_non_video_upper = (video_mbps < 8.0)  # soglia 8 Mbps
                self.logger.info("Video=%.2f Mbps - allow_non_video_upper=%s",
                                 video_mbps, self.allow_non_video_upper)
            hub.sleep(1)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath   # lo switch
        parser = dp.ofproto_parser
        ofp = dp.ofproto

        # Proactive match per traffico video
        match_video_dst = parser.OFPMatch(
            eth_type=0x0800,   # IPv4
            ip_proto=17,       # UDP
            udp_dst=9999
        )
        match_video_src = parser.OFPMatch(
            eth_type=0x0800,
            ip_proto=17,
            udp_src=9999
        )

        actions_video = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER,
                                                ofp.OFPCML_NO_BUFFER)]

        self.add_flow(dp, 15, match_video_dst, actions_video)
        self.add_flow(dp, 15, match_video_src, actions_video)

        # Regola di default
        match_default = parser.OFPMatch()
        actions_default = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER,
                                                  ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, 0, match_default, actions_default)

    def add_flow(self, datapath, priority, match, actions, buffer_id=None):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath,
            buffer_id=buffer_id if buffer_id is not None else ofproto.OFP_NO_BUFFER,
            priority=priority,
            match=match,
            instructions=inst
        )
        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        dpid = dp.id
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)  # estrae il pacchetto
        eth = pkt.get_protocol(ethernet.ethernet)
        if not eth:  # ignora traffico non Ethernet
            return

        src = eth.src
        dst = eth.dst

        # MAC learning
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        ip_pkt  = pkt.get_protocol(ipv4.ipv4)
        udp_pkt = pkt.get_protocol(udp.udp)

        # Flag se pacchetto è video (UDP porta 9999)
        is_video = bool(ip_pkt and udp_pkt and
                        (udp_pkt.dst_port == 9999 or udp_pkt.src_port == 9999))

        if is_video:
            self._video_bytes += len(msg.data)

        if dpid in (1, 4):  # Access switches
            if dst in self.mac_to_port[dpid]:
                out_port = self.mac_to_port[dpid][dst]

                # Se è un host locale
                if out_port in self.HOST_PORTS[dpid]:
                    if is_video:
                        queue_id = self.QUEUE_HIGH
                    else:
                        queue_id = self.QUEUE_LOW
                    actions = [
                        parser.OFPActionSetQueue(queue_id),
                        parser.OFPActionOutput(out_port)
                    ]
                else:
                    # Verso backbone: scegli porta e coda
                    if is_video:
                        out_port = self.PORT_S2     # upper slice obbligatoria
                        queue_id = self.QUEUE_HIGH
                    else:
                        if self.allow_non_video_upper:
                            out_port = self.PORT_S2  # upper slice consentita
                            queue_id = self.QUEUE_LOW
                        else:
                            out_port = self.PORT_S3  # lower slice
                            queue_id = self.QUEUE_LOW
                    actions = [
                        parser.OFPActionSetQueue(queue_id),
                        parser.OFPActionOutput(out_port)
                    ]
            else:
                # Flood iniziale se MAC sconosciuto
                flood_ports = list(self.HOST_PORTS[dpid])
                if is_video:
                    flood_ports.append(self.PORT_S2)
                    queue_id = self.QUEUE_HIGH
                else:
                    if self.allow_non_video_upper:
                        flood_ports.append(self.PORT_S2)
                        queue_id = self.QUEUE_LOW
                    else:
                        flood_ports.append(self.PORT_S3)
                        queue_id = self.QUEUE_LOW

                actions = (
                    [parser.OFPActionSetQueue(queue_id)] +
                    [parser.OFPActionOutput(p) for p in flood_ports]
                )

        else:  # Backbone switches (s2, s3)
            if dst in self.mac_to_port[dpid]:
                out_port = self.mac_to_port[dpid][dst]
                if is_video:
                    queue_id = self.QUEUE_HIGH
                else:
                    queue_id = self.QUEUE_LOW

                actions = [
                    parser.OFPActionSetQueue(queue_id),
                    parser.OFPActionOutput(out_port)
                ]

                match = parser.OFPMatch(
                    in_port=in_port,
                    eth_src=src,
                    eth_dst=dst
                )

                priority = self.PRIORITY_VIDEO if is_video else self.PRIORITY_DEFAULT

                if msg.buffer_id != ofp.OFP_NO_BUFFER:
                    self.add_flow(dp, priority, match, actions, msg.buffer_id)
                    return
                else:
                    self.add_flow(dp, priority, match, actions)
            else:
                if is_video:
                    queue_id = self.QUEUE_HIGH
                else:
                    queue_id = self.QUEUE_LOW

                actions = [
                    parser.OFPActionSetQueue(queue_id),
                    parser.OFPActionOutput(ofp.OFPP_FLOOD)
                ]

        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=dp, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions,
            data=data
        )
        dp.send_msg(out)
