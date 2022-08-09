# MOX-HA-Integration
Making MOX smart home to work with Home Assistant.<br /><br />

Mox is an Australian company which develops smart home control devices.<br />
However its devices doesn't work with IOT protocols.<br />
You can learn more about Mox at their web site: http://www.mox.com.au/lt/index.htm<br />
When we built our home at 2014 we used Mox for constructing the smart home we wanted to have.<br />
Most of the lights, and all ceiling ventilators, air conditioners, and window shutters are controlled centrally from the electricity board by Mox controllers.<br />
The main problem was with the 10" touch screen which controls the system, It stored all the automations scripts.<br />
Then one day the screen stopped working. we could still use the wall switches to turn on and off things, but all the automations we planed didn't work anymore.<br />
Fortunately I had another 7" screen in the 2nd flore, so I connected my laptop to it, and started sniffing the network traffic (using wireshark).<br />
We have two types of controllers, a simple on/off for lights, ceiling ventilators etc. And a curtain controller which you can set the percentage off window covering with.<br />
Each controller has a representing number and has 8 output contactors which are numbered from 17 to 24.<br />
I found out that the system is using UDP protocol and the message sent was quite easy to understand.<br />
The message started with a command type prefix (0x03 for sending command), the controller number and the output number (both hex), the percentage of window covering for curtain controller or 1/0 for on or off for the simple controller.<br />
I started with sending a fake message using few lines off python code and it worked. I could turn my office light on and off.<br />
I allso found that after sending the system a message, for few minutes it keeps sending back the status off (randomly selected) devices, and reports immediately on every device status change.<br />
Then after few tries and fails, I found Home Assistant and app-daemon.<br />
In Home Assistant configuration file, I declared Boolean variables for each on/off device, and a number variable for each curtain.<br />
I wrote two little services. One that tracks every change in the variables values and sends a command to the represented device to fallow the change. And another one that listen to system messages, and whenever a device status is changed it changes the representing variable accordingly.<br />
I added a separate file with a dictionarry off all my home devices, includig name, type, adress and port.<br />
Now every change of any variable on my Home Assistant dashboard is turning devices on or off, and avery manual change (using wall switches) is represented amidiatly on my Home Assistant dashboard.<br />
I can now do much more clever automations with my smart home than I could before.<br />
The joy came back to my life :-), Hope it can help you too.
