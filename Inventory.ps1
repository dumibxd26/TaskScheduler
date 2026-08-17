$cpu = Get-CimInstance Win32_Processor
$cpu_name = $cpu.Name
$cpu_arch = switch ($cpu.Architecture) {
    0 { "x86" }
    9 { "x64" }
    5 { "ARM" }
    12 { "ARM64" }
    default { "Unknown" }
}
$cpu_cores = $cpu.NumberOfCores
$cpu_logical = $cpu.NumberOfLogicalProcessors
$cpu_max_clock = $cpu.MaxClockSpeed
$cpu_current_clock = $cpu.CurrentClockSpeed
$cpu_l2 = $cpu.L2CacheSize
$cpu_l3 = $cpu.L3CacheSize

$comp_sys = Get-CimInstance Win32_ComputerSystem
$virt_enabled = $comp_sys.HypervisorPresent

$mem_modules = Get-CimInstance Win32_PhysicalMemory | ForEach-Object {
    [PSCustomObject]@{
        Manufacturer = $_.Manufacturer
        PartNumber = $_.PartNumber
        CapacityGB = [Math]::Round($_.Capacity / 1GB, 2)
        ConfiguredSpeed = $_.ConfiguredClockSpeed
        DataWidth = $_.DataWidth
    }
}
$total_ram = [Math]::Round(($mem_modules | Measure-Object -Property CapacityGB -Sum).Sum, 2)

$phys_disks = Get-CimInstance Win32_DiskDrive | ForEach-Object {
    [PSCustomObject]@{
        FriendlyName = $_.Caption
        Model = $_.Model
        MediaType = "HDD/SSD (CIM-reported)"
        BusType = $_.InterfaceType
        SizeGB = [Math]::Round($_.Size / 1GB, 2)
    }
}

$drive_d = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='D:'"
$drive_d_free = if ($drive_d) { [Math]::Round($drive_d.FreeSpace / 1GB, 2) } else { "Not Found (Unavailable)" }

$net_adapters = Get-CimInstance Win32_NetworkAdapter -Filter "PhysicalAdapter=True" | Where-Object { $_.NetConnectionStatus -eq 2 } | ForEach-Object {
    $link_speed = if ($_.Speed) { "$([Math]::Round($_.Speed / 1MB, 1)) Mbps" } else { "Unknown" }
    $type = if ($_.Name -match "Wi-Fi|Wireless|802.11" -or $_.Description -match "Wi-Fi|Wireless|802.11") { "Wi-Fi" } else { "Ethernet" }
    [PSCustomObject]@{
        Description = $_.Description
        Speed = $link_speed
        Type = $type
        Status = "Connected"
    }
}

$gpus = Get-CimInstance Win32_VideoController | ForEach-Object {
    $ram_bytes = $_.AdapterRAM
    $ram_gb = if ($ram_bytes -and $ram_bytes -gt 0) { 
        [Math]::Round($ram_bytes / 1GB, 2) 
    } elseif ($ram_bytes -and $ram_bytes -lt 0) {
        $unsigned_ram = [uint32]$ram_bytes
        [Math]::Round($unsigned_ram / 1GB, 2)
    } else { 
        "Unavailable (dynamic/system allocated)" 
    }
    [PSCustomObject]@{
        Name = $_.Name
        MemoryGB = $ram_gb
    }
}

$battery = Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue
$power_plan = Get-CimInstance -Namespace root\cimv2\power -ClassName Win32_PowerPlan -ErrorAction SilentlyContinue | Where-Object { $_.IsActive }

$thermal_zones = Get-CimInstance -Namespace root\wmi -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction SilentlyContinue | ForEach-Object {
    $temp_c = ($_.CurrentTemperature / 10) - 273.15
    [PSCustomObject]@{
        Instance = $_.InstanceName
        TemperatureC = [Math]::Round($temp_c, 2)
    }
}

Write-Host "=== CPU ==="
Write-Host "Name: $cpu_name (Firmware Value)"
Write-Host "Architecture: $cpu_arch (Firmware Value)"
Write-Host "Cores: $cpu_cores (Firmware Value)"
Write-Host "Logical Processors: $cpu_logical (Firmware Value)"
Write-Host "Max Clock: $cpu_max_clock MHz (Firmware Value)"
Write-Host "Current Clock: $cpu_current_clock MHz (Measured/Runtime Value)"
Write-Host "L2 Cache: $cpu_l2 KB (Firmware Value)"
Write-Host "L3 Cache: $cpu_l3 KB (Firmware Value)"
Write-Host "Virtualization Enabled: $virt_enabled (Firmware Value)"
Write-Host ""
Write-Host "=== Memory ==="
Write-Host "Total RAM: $total_ram GB (Measured/Runtime Value)"
$mem_modules | ForEach-Object {
    Write-Host "Module: Manufacturer=$($_.Manufacturer), Part=$($_.PartNumber), Size=$($_.CapacityGB) GB, Speed=$($_.ConfiguredSpeed) MHz, Width=$($_.DataWidth) bits (Firmware Values)"
}
Write-Host ""
Write-Host "=== Disks ==="
$phys_disks | ForEach-Object {
    Write-Host "Disk: Friendly=$($_.FriendlyName), Model=$($_.Model), Size=$($_.SizeGB) GB, Bus=$($_.BusType) (Firmware Values)"
}
Write-Host "Logical Free Space D: $drive_d_free GB (Measured/Runtime Value)"
Write-Host ""
Write-Host "=== Network ==="
$net_adapters | ForEach-Object {
    Write-Host "Adapter: Desc=$($_.Description) (Firmware Value), Type=$($_.Type) (Firmware Value), LinkSpeed=$($_.Speed) (Runtime Value), Status=$($_.Status) (Runtime Value)"
}
Write-Host ""
Write-Host "=== GPU ==="
$gpus | ForEach-Object {
    Write-Host "GPU: Name=$($_.Name) (Firmware Value), Memory=$($_.MemoryGB) GB"
}
Write-Host ""
Write-Host "=== Battery / Power ==="
if ($battery) {
    Write-Host "Battery Present (Firmware Value)"
} else {
    Write-Host "No battery detected (Runtime Value)"
}
if ($power_plan) {
    Write-Host "Active Plan: $($power_plan.ElementName) (Runtime Value)"
} else {
    Write-Host "Active Plan: Unavailable"
}
Write-Host ""
Write-Host "=== Thermal ==="
if ($thermal_zones) {
    $thermal_zones | ForEach-Object {
        Write-Host "Zone: $($_.Instance) Temp=$($_.TemperatureC) C (Runtime Value)"
    }
} else {
    Write-Host "Thermal Sensors: Unavailable / Not exposed"
}
