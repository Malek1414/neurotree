//! Power state from `pmset`: on battery a change-triggered reindex waits 3x longer, in Low Power Mode 6x.
//! pmset is only asked when a reindex is otherwise due (at most every 10 minutes), never polled.

use std::process::Command;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Power {
    Ac,
    Battery,
    LowPower,
}

/// `pmset -g batt` says "Now drawing from 'Battery Power'"; `pmset -g` lists "lowpowermode 1" when it is on.
pub fn parse(batt: &str, settings: &str) -> Power {
    let low = settings.lines().any(|l| {
        let mut it = l.split_whitespace();
        it.next() == Some("lowpowermode") && it.next() == Some("1")
    });
    if low {
        Power::LowPower
    } else if batt.contains("'Battery Power'") {
        Power::Battery
    } else {
        Power::Ac
    }
}

fn pmset(args: &[&str]) -> String {
    Command::new("/usr/bin/pmset")
        .args(args)
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).into_owned())
        .unwrap_or_default()
}

/// The current state; a Mac without pmset output counts as plugged in.
pub fn current() -> Power {
    parse(&pmset(&["-g", "batt"]), &pmset(&["-g"]))
}

/// How many times the normal reindex interval to wait.
pub fn factor(p: Power) -> u32 {
    match p {
        Power::Ac => 1,
        Power::Battery => 3,
        Power::LowPower => 6,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_pmset_output() {
        let batt = "Now drawing from 'Battery Power'\n -InternalBattery-0 (id=1)\t92%; discharging; 5:30 remaining present: true\n";
        let ac = "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t100%; charged; 0:00 remaining present: true\n";
        assert_eq!(parse(batt, " lowpowermode         0\n"), Power::Battery);
        assert_eq!(parse(batt, " lowpowermode         1\n"), Power::LowPower);
        assert_eq!(parse(ac, " lowpowermode         0\n"), Power::Ac);
        assert_eq!(parse("", ""), Power::Ac, "no pmset: treat as plugged in");
        assert_eq!(parse(ac, " lowpowermode         1\n"), Power::LowPower, "Low Power Mode can be on while charging");
    }

    #[test]
    fn battery_waits_longer() {
        assert_eq!((factor(Power::Ac), factor(Power::Battery), factor(Power::LowPower)), (1, 3, 6));
    }
}
