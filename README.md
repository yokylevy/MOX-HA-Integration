# MOX-HA-Integration
Making MOX smart home to work with Home Assistant.

Mox is an Australian company which develops smart home control devices.
However its devices doesn't work with IOT protocols.
You can learn more about Mox at their web site: http://www.mox.com.au/lt/index.htm
When we built our home at 2014 we used Mox for constructing the smart home we wanted to have.
Most of the lights, and all ceiling ventilators, air conditioners, and window shutters are controlled centrally from the electricity board by Mox controllers.
The main problem was with the 10" touch screen which controls the system, It stored all the automations scripts.
Then one day the screen stopped working. we could still use the wall switches to turn on and off things, but all the automations we planed didn't work anymore.
Fortunately I had another 7" screen in the 2nd flore, so I connected my laptop to it, and started sniffing the network traffic (using wireshark).
We have two types of controllers, a simple on/off for lights, ceiling ventilators etc. And a curtain controller which you can set the percentage off window covering with.
Each controller has a representing number and has 8 output contactors which are numbered from 17 to 24.
I found out that the system is using UDP protocol and the message sent was quite easy to understand.
The message started with a command type prefix (0x03 for sending command), the controller number and the output number (both hex), the percentage of window covering for curtain controller or 1/0 for on or off for the simple controller.
I started with sending a fake message using few lines off python code and it worked. I could turn my office light on and off.
I allso found that after sending the system a message, for few minutes it keeps sending back the status off (randomly selected) devices, and reports immediately on every device status change.
Then after few tries and fails, I found Home Assistant and app-daemon.
In Home Assistant configuration file, I declared Boolean variables for each on/off device, and a number variable for each curtain.
I wrote two little services. One that tracks every change in the variables values and sends a command to the represented device to fallow the change. And another one that listen to system messages, and whenever a device status is changed it changes the representing variable accordingly.
I added a separate file with a dictionarry off all my home devices, includig name, type, adress and port.
Now every change of any variable on my Home Assistant dashboard is turning devices on or off, and avery manual change (using wall switches) is represented amidiatly on my Home Assistant dashboard.
I can now do much more clever automations with my smart home than I could before.
The joy came back to my life :-), Hope it can help you too.