# Installing the timers on millie

    mkdir -p ~/.config/systemd/user
    cp ~/buffet-bot/systemd/* ~/.config/systemd/user/
    systemctl --user daemon-reload

    systemctl --user enable --now tradingview-cdp.service
    systemctl --user enable --now buffet-bot-premarket.timer
    systemctl --user enable --now buffet-bot-alpha.timer
    systemctl --user enable --now buffet-bot-postclose.timer

    # Without this, user services die when you log out. A headless mini PC is
    # logged out almost always, so this line is not optional.
    loginctl enable-linger $USER

Check the schedule, and that the timezone resolved the way you expect:

    systemctl --user list-timers 'buffet-bot*'

Run one by hand without waiting for its timer:

    systemctl --user start buffet-bot@premarket.service
    journalctl --user -u buffet-bot@premarket.service -f

Or bypass systemd entirely:

    cd ~/buffet-bot && ./run-cycle.sh premarket

## Check the chromium binary name

The unit assumes `/usr/bin/chromium-browser`. Fedora sometimes installs it as
`/usr/bin/chromium`. Confirm with `which chromium chromium-browser` and edit
`tradingview-cdp.service` to match, or the service will restart-loop forever.
