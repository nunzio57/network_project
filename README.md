# network_project
# Network Slicing in SDN with Ryu and Mininet  
### Course Project – Network and Cloud Infrastructures  
M.Sc. in Computer Engineering — University of Naples Federico II (A.Y. 2024/2025)

This repository contains the project developed for the *Network and Cloud Infrastructures* course.  
The work implements static, service-based, and dynamic network slicing in an SDN environment using Mininet as the network emulator and Ryu as the SDN controller.

---

## Overview

The goal of the project is to design and evaluate different slicing mechanisms within a Software Defined Network.  
The implementation focuses on three approaches:

### Topology-Based Slicing (Static Slicing)
A rigid logical separation between two slices:  
- H1 ↔ H3 → upper slice
- H2 ↔ H4 → lower slice
All other flows are blocked.  
The controller enforces:
- slice violation detection  
- MAC learning  
- ARP filtering per slice  
- OpenFlow 1.3 FlowMod/PacketOut logic  

---

### Service-Based Slicing (Traffic-Based)
Traffic is classified based on protocol and port:
- Video traffic (UDP, port 9999) → forwarded on the upper slice with high priority
- Non-video traffic → forwarded on the lower slice  

The controller:
- inspects L3/L4 headers  
- installs high/low-priority rules  
- uses QoS queues for priority management  
- performs guided flood on unknown destinations  

---

### Dynamic Slicing (Adaptive Policy)
Non-video traffic can temporarily use the upper slice only if video traffic < 8 Mbit/s.  
A monitoring thread periodically measures the bandwidth used by video flows and updates routing decisions.

Features:
- real-time throughput estimation  
- dynamic enable/disable of upper-slice sharing  
- queue-based prioritization  
- reactive rule installation
